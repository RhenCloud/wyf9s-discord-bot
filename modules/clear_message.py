import re
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch

import discord
from loguru import logger as l

from config import ConfigModel
from i18n import t as _t
from modules.audit import AuditLogger

CLEAR_MESSAGE_MARKER = "[clear-message]"


def _parse_time_or_id(
    value: str | None, label: str, lang: str = "zh"
) -> tuple[discord.Object | None, datetime | None, str | None]:
    if not value or not value.strip():
        return None, None, None

    value = value.strip()

    if value.isdigit():
        return discord.Object(id=int(value)), None, None

    m = re.fullmatch(r"(\d+[dhm])+", value)
    if m:
        td = timedelta()
        for num, unit in re.findall(r"(\d+)([dhm])", value):
            n = int(num)
            if unit == "d":
                td += timedelta(days=n)
            elif unit == "h":
                td += timedelta(hours=n)
            elif unit == "m":
                td += timedelta(minutes=n)
        return None, datetime.now(timezone.utc) - td, None

    try:
        cleaned = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return None, dt, None
    except ValueError:
        pass

    return (
        None,
        None,
        _t("clearmsg.time_invalid", lang, label=label, value=value),
    )


def _thread_created(thread: discord.Thread) -> datetime:
    """Thread creation time (derived from the snowflake ID, always available)."""
    return discord.utils.snowflake_time(thread.id)


def _thread_last_activity(thread: discord.Thread) -> datetime:
    """Best-effort 'last activity' timestamp for ordering / time-window filtering."""
    if thread.last_message_id:
        return discord.utils.snowflake_time(thread.last_message_id)
    ts = getattr(thread, "archive_timestamp", None)
    if ts is not None:
        return ts
    return _thread_created(thread)


class ClearMessageService:
    def __init__(
        self,
        config: ConfigModel,
        client: discord.Client,
        audit: AuditLogger | None,
    ):
        self.c = config
        self.client = client
        self.audit = audit

    async def do_clear_message(
        self,
        author: discord.User | discord.Member,
        guild: discord.Guild | None,
        channel: discord.abc.GuildChannel
        | discord.abc.PrivateChannel
        | discord.Thread
        | None,
        user: discord.User | None = None,
        user_ids: str | None = None,
        webhook_ids: str | None = None,
        nick_pattern: str | None = None,
        content_pattern: str | None = None,
        message_count: int | None = None,
        within_minutes: int | None = None,
        scope: str = "channel",
        channel_target: discord.abc.GuildChannel | None = None,
        start: str | None = None,
        end: str | None = None,
        delete_threads: bool = False,
        forum_scan_count: int | None = None,
        write_audit: bool = True,
        lang: str = "zh",
    ) -> str:
        message_limit = message_count if message_count is not None else 0
        minutes_limit = within_minutes if within_minutes is not None else 0
        forum_scan_limit = forum_scan_count if forum_scan_count is not None else None

        if message_limit < 0:
            return _t("clearmsg.count_negative", lang)

        if minutes_limit < 0:
            return _t("clearmsg.minutes_negative", lang)

        if forum_scan_limit is not None and forum_scan_limit < 0:
            return _t("clearmsg.forum_scan_negative", lang)

        has_time_range = bool(start or end)
        has_legacy_restriction = minutes_limit > 0 or message_limit > 0
        has_match_filter = bool(
            user
            or (user_ids and user_ids.strip())
            or (webhook_ids and webhook_ids.strip())
            or (nick_pattern and nick_pattern.strip())
            or (content_pattern and content_pattern.strip())
        )

        # 特例: delete_threads + forum_scan_count 且无其它区间/数量限制时,
        # forum_scan_count 本身即作为限制 (仅扫描论坛帖子, 不扫描普通频道消息,
        # 以免无限制地拉取频道历史)。
        forum_only_mode = (
            delete_threads
            and forum_scan_limit is not None
            and not has_time_range
            and not has_legacy_restriction
        )

        if has_time_range and has_legacy_restriction:
            return _t("clearmsg.range_conflict", lang)

        if (
            not has_time_range
            and message_limit == 0
            and minutes_limit == 0
            and not forum_only_mode
        ):
            return _t("clearmsg.need_limit", lang)

        if not has_time_range and not has_legacy_restriction and not has_match_filter:
            return _t("clearmsg.need_filter", lang)

        def parse_ids(raw_ids: str, label: str) -> tuple[set[int] | None, str]:
            parts = [
                part.strip()
                for part in raw_ids.replace("，", ",").split(",")
                if part.strip()
            ]
            if not parts:
                return None, _t("clearmsg.ids_empty", lang, label=label)
            values: set[int] = set()
            for part in parts:
                if not part.isdigit():
                    return None, _t(
                        "clearmsg.ids_invalid", lang, label=label, part=part
                    )
                values.add(int(part))
            return values, ""

        target_user_ids: set[int] = set()
        target_webhook_ids: set[int] = set()
        nick_pattern_filter: str | None = None
        content_pattern_filter: str | None = None
        match_types: list[str] = []

        if user:
            target_user_ids = {user.id}
            match_types.append("user")
        if user_ids and user_ids.strip():
            parsed, err = parse_ids(user_ids.strip(), "user_ids")
            if not parsed:
                return _t("clearmsg.error_wrap", lang, msg=err)
            target_user_ids |= parsed
            if "user_ids" not in match_types:
                match_types.append("user_ids")
        if webhook_ids and webhook_ids.strip():
            parsed, err = parse_ids(webhook_ids.strip(), "webhook_ids")
            if not parsed:
                return _t("clearmsg.error_wrap", lang, msg=err)
            target_webhook_ids = parsed
            match_types.append("webhook_ids")
        if nick_pattern and nick_pattern.strip():
            nick_pattern_filter = nick_pattern.strip()
            match_types.append("nick_pattern")
        if content_pattern and content_pattern.strip():
            content_pattern_filter = content_pattern.strip()
            match_types.append("content_pattern")

        scope = scope.lower().strip()
        if scope not in ["channel", "server"]:
            return _t("clearmsg.invalid_scope", lang, scope=scope)

        target_channels: list[
            discord.TextChannel
            | discord.VoiceChannel
            | discord.StageChannel
            | discord.Thread
        ] = []
        target_forums: list[discord.ForumChannel] = []
        if scope == "channel":
            primary = channel_target or channel
            if isinstance(primary, discord.ForumChannel):
                target_forums.append(primary)
            else:
                target_channels.append(primary)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
        else:
            if not guild:
                return _t("clearmsg.server_only", lang)
            target_channels = [
                ch
                for ch in guild.channels
                if isinstance(
                    ch,
                    (discord.TextChannel, discord.VoiceChannel, discord.StageChannel),
                )
            ]
            target_forums = [
                ch for ch in guild.channels if isinstance(ch, discord.ForumChannel)
            ]

        # forum_only_mode: 仅扫描论坛帖子, 不扫描普通频道消息。
        # 显式指定论坛频道 -> 仅该论坛; 否则 -> 服务器内所有论坛。
        if forum_only_mode:
            target_channels = []
            if isinstance(channel_target, discord.ForumChannel):
                target_forums = [channel_target]
            else:
                if not guild:
                    return _t("clearmsg.server_only", lang)
                target_forums = [
                    ch for ch in guild.channels if isinstance(ch, discord.ForumChannel)
                ]

        start_obj: discord.Object | None = None
        start_dt: datetime | None = None
        end_obj: discord.Object | None = None
        end_dt: datetime | None = None
        if has_time_range:
            if start:
                start_obj, start_dt, start_err = _parse_time_or_id(start, "start", lang)
                if start_err:
                    return _t("clearmsg.error_wrap", lang, msg=start_err)
            if end:
                end_obj, end_dt, end_err = _parse_time_or_id(end, "end", lang)
                if end_err:
                    return _t("clearmsg.error_wrap", lang, msg=end_err)
            if start_dt and end_dt and start_dt > end_dt:
                return _t("clearmsg.start_gt_end_time", lang)

        # start / end 若为 ID, 先尝试匹配消息, 匹配不到再匹配帖子 (Thread)。
        # 两者资源类型必须一致, 且帖子必须位于同一论坛; 命中帖子时进入
        # "帖子范围模式" (thread_range_mode), 按帖子创建时间作为区间边界。
        start_msg: discord.Message | None = None
        end_msg: discord.Message | None = None
        thread_range_mode = False
        thread_created_start: datetime | None = None
        thread_created_end: datetime | None = None

        message_fetch_channel = (
            target_channels[0] if scope == "channel" and target_channels else None
        )

        async def resolve_ref(
            obj_id: int, label: str
        ) -> tuple[str | None, discord.Message | discord.Thread | None, str | None]:
            # 优先匹配消息
            if message_fetch_channel is not None:
                try:
                    msg = await message_fetch_channel.fetch_message(obj_id)
                    return "message", msg, None
                except discord.NotFound:
                    pass
                except discord.HTTPException as e:
                    return None, None, _t("clearmsg.ref_fetch_error", lang, error=e)
            # 再匹配帖子 (Thread)
            thread: discord.Thread | None = None
            if guild is not None:
                thread = guild.get_thread(obj_id)
                if thread is None:
                    try:
                        fetched = await guild.fetch_channel(obj_id)
                    except (
                        discord.NotFound,
                        discord.Forbidden,
                        discord.HTTPException,
                    ):
                        fetched = None
                    if isinstance(fetched, discord.Thread):
                        thread = fetched
            if thread is not None:
                return "thread", thread, None
            return (
                None,
                None,
                _t("clearmsg.ref_not_found", lang, label=label, id=obj_id),
            )

        if start_obj or end_obj:
            start_kind = end_kind = None
            start_ref: discord.Message | discord.Thread | None = None
            end_ref: discord.Message | discord.Thread | None = None
            if start_obj:
                start_kind, start_ref, err = await resolve_ref(start_obj.id, "start")
                if err:
                    return _t("clearmsg.error_wrap", lang, msg=err)
            if end_obj:
                end_kind, end_ref, err = await resolve_ref(end_obj.id, "end")
                if err:
                    return _t("clearmsg.error_wrap", lang, msg=err)

            if start_obj and end_obj and start_kind != end_kind:
                return _t("clearmsg.ref_type_mismatch", lang)

            ref_kind = start_kind or end_kind
            if ref_kind == "thread":
                if not delete_threads:
                    return _t("clearmsg.thread_range_needs_flag", lang)
                start_thread = (
                    start_ref if isinstance(start_ref, discord.Thread) else None
                )
                end_thread = end_ref if isinstance(end_ref, discord.Thread) else None
                forum = None
                for th in (start_thread, end_thread):
                    if th is None:
                        continue
                    parent = th.parent
                    if not isinstance(parent, discord.ForumChannel):
                        return _t("clearmsg.thread_not_forum", lang, id=th.id)
                    if forum is None:
                        forum = parent
                    elif forum.id != parent.id:
                        return _t("clearmsg.threads_diff_forum", lang)
                if forum is None:
                    return _t("clearmsg.thread_not_forum", lang, id=0)
                if (
                    start_thread is not None
                    and end_thread is not None
                    and _thread_created(start_thread) > _thread_created(end_thread)
                ):
                    return _t("clearmsg.start_gt_end_thread", lang)
                thread_range_mode = True
                thread_created_start = (
                    _thread_created(start_thread) if start_thread is not None else None
                )
                thread_created_end = (
                    _thread_created(end_thread) if end_thread is not None else None
                )
                # 帖子范围模式: 目标锁定为该论坛
                target_channels = []
                target_forums = [forum]
            elif ref_kind == "message":
                start_msg = (
                    start_ref if isinstance(start_ref, discord.Message) else None
                )
                end_msg = end_ref if isinstance(end_ref, discord.Message) else None
                if (
                    start_msg is not None
                    and end_msg is not None
                    and start_msg.created_at > end_msg.created_at
                ):
                    return _t("clearmsg.start_gt_end_msg", lang)

        cutoff_time = None
        start_time_bound: datetime | None = None
        end_time_bound: datetime | None = None
        if has_time_range:
            start_time_bound = start_dt
            end_time_bound = end_dt
        elif minutes_limit > 0:
            cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=minutes_limit)

        now = datetime.now(timezone.utc)

        checked_messages_by_channel: dict[int, list[discord.Message]] = {}
        checked_count = 0

        def message_matches(msg: discord.Message) -> bool:
            bot_user = self.client.user
            if (
                bot_user is not None
                and msg.author.id == bot_user.id
                and CLEAR_MESSAGE_MARKER in msg.content
            ):
                return False
            if cutoff_time is not None and msg.created_at < cutoff_time:
                return False
            if start_time_bound is not None and msg.created_at < start_time_bound:
                return False
            if end_time_bound is not None and msg.created_at > end_time_bound:
                return False
            if not match_types:
                return True
            if target_user_ids and msg.author.id in target_user_ids:
                return True
            if (
                target_webhook_ids
                and msg.webhook_id is not None
                and msg.webhook_id in target_webhook_ids
            ):
                return True
            if nick_pattern_filter:
                display_name = getattr(msg.author, "display_name", msg.author.name)
                if fnmatch(display_name, nick_pattern_filter):
                    return True
            if content_pattern_filter and fnmatch(msg.content, content_pattern_filter):
                return True
            return False

        history_kwargs: dict = {}
        if has_time_range:
            history_kwargs["limit"] = None
            if end_obj:
                history_kwargs["before"] = end_obj
            elif end_dt:
                history_kwargs["before"] = end_dt
            if start_obj:
                history_kwargs["after"] = start_obj
            elif start_dt:
                history_kwargs["after"] = start_dt
        else:
            history_kwargs["limit"] = message_limit if message_limit > 0 else None

        for target_channel in target_channels:
            try:
                async for msg in target_channel.history(**history_kwargs):
                    if cutoff_time is not None and msg.created_at < cutoff_time:
                        break
                    if (
                        start_time_bound is not None
                        and msg.created_at < start_time_bound
                    ):
                        break
                    if message_matches(msg):
                        checked_messages_by_channel.setdefault(
                            target_channel.id, []
                        ).append(msg)
                        checked_count += 1
            except discord.Forbidden:
                pass
            except Exception as e:
                l.warning(
                    f"[clear-message] Error fetching messages from {target_channel.name} ({target_channel.id}): {e}"
                )

        # 将 start_msg 和 end_msg 添加到结果中（如果它们符合过滤条件且尚未包含）
        if start_msg and end_msg and scope == "channel" and target_channels:
            channel_id = target_channels[0].id
            existing_messages = checked_messages_by_channel.get(channel_id, [])
            if start_msg not in existing_messages and message_matches(start_msg):
                checked_messages_by_channel.setdefault(channel_id, []).append(start_msg)
                checked_count += 1
            if end_msg not in existing_messages and message_matches(end_msg):
                checked_messages_by_channel.setdefault(channel_id, []).append(end_msg)
                checked_count += 1

        # ===== 论坛 (ForumChannel) 处理 =====
        # 行为 A: 扫描论坛内帖子, 清除其中匹配的消息。
        # 行为 B: delete_threads=True 时, 直接删除由目标用户所作 (匹配) 的整个帖子
        #         (会顺带删除帖子内所有消息, 因此需显式开启)。
        threads_to_delete: list[discord.Thread] = []
        scanned_thread_channels: dict[int, discord.Thread] = {}
        threads_scanned_count = 0

        # 帖子活跃时间窗口 (行为 A/B 的帖子筛选)
        lower_activity_bound: datetime | None = None
        upper_activity_bound: datetime | None = None
        if not thread_range_mode:
            if has_time_range:
                lower_activity_bound = start_time_bound
                upper_activity_bound = end_time_bound
            elif minutes_limit > 0:
                lower_activity_bound = cutoff_time

        window_mode = thread_range_mode or has_time_range or minutes_limit > 0
        if forum_scan_limit is not None:
            thread_scan_cap: int | None = forum_scan_limit
        elif window_mode:
            thread_scan_cap = None
        else:
            thread_scan_cap = message_limit if message_limit > 0 else None

        # 归档帖子按活跃时间倒序返回, 时间窗口模式下可提前 break
        break_bound = (
            lower_activity_bound if (window_mode and not thread_range_mode) else None
        )

        def thread_in_window(th: discord.Thread) -> bool:
            tcs = thread_created_start
            tce = thread_created_end
            lo = lower_activity_bound
            hi = upper_activity_bound
            if thread_range_mode:
                ca = _thread_created(th)
                if tcs is not None and ca < tcs:
                    return False
                if tce is not None and ca > tce:
                    return False
                return True
            la = _thread_last_activity(th)
            if lo is not None and la < lo:
                return False
            if hi is not None and _thread_created(th) > hi:
                return False
            return True

        def thread_matches(th: discord.Thread) -> bool:
            if not match_types:
                return True
            if target_user_ids and th.owner_id in target_user_ids:
                return True
            if nick_pattern_filter:
                owner = th.owner
                owner_name = getattr(owner, "display_name", None) if owner else None
                if owner_name and fnmatch(owner_name, nick_pattern_filter):
                    return True
            if content_pattern_filter and fnmatch(
                th.name or "", content_pattern_filter
            ):
                return True
            return False

        async def process_thread(th: discord.Thread) -> None:
            nonlocal checked_count
            if not thread_in_window(th):
                return
            if delete_threads and thread_matches(th):
                threads_to_delete.append(th)
                return
            try:
                async for msg in th.history(**history_kwargs):
                    if cutoff_time is not None and msg.created_at < cutoff_time:
                        break
                    if (
                        start_time_bound is not None
                        and msg.created_at < start_time_bound
                    ):
                        break
                    if message_matches(msg):
                        checked_messages_by_channel.setdefault(th.id, []).append(msg)
                        scanned_thread_channels[th.id] = th
                        checked_count += 1
            except discord.Forbidden:
                pass
            except Exception as e:
                l.warning(
                    f"[clear-message] Error fetching thread messages ({th.id}): {e}"
                )

        for forum in target_forums:
            examined = 0
            stop = False
            for th in sorted(forum.threads, key=_thread_last_activity, reverse=True):
                if thread_scan_cap is not None and examined >= thread_scan_cap:
                    stop = True
                    break
                examined += 1
                threads_scanned_count += 1
                await process_thread(th)
            if not stop:
                try:
                    async for th in forum.archived_threads(limit=None):
                        if thread_scan_cap is not None and examined >= thread_scan_cap:
                            break
                        if (
                            break_bound is not None
                            and _thread_last_activity(th) < break_bound
                        ):
                            break
                        examined += 1
                        threads_scanned_count += 1
                        await process_thread(th)
                except (discord.Forbidden, discord.HTTPException):
                    pass

        if checked_count == 0 and not threads_to_delete:
            match_descs: list[str] = []
            if target_user_ids:
                if len(target_user_ids) == 1 and match_types == ["user"]:
                    match_descs.append(
                        _t(
                            "clearmsg.desc_user",
                            lang,
                            user=user.mention
                            if user
                            else _t("clearmsg.unknown_user", lang),
                        )
                    )
                else:
                    match_descs.append(
                        _t("clearmsg.desc_user_ids", lang, ids=sorted(target_user_ids))
                    )
            if target_webhook_ids:
                match_descs.append(
                    _t(
                        "clearmsg.desc_webhook_ids",
                        lang,
                        ids=sorted(target_webhook_ids),
                    )
                )
            if nick_pattern_filter:
                match_descs.append(
                    _t("clearmsg.desc_nick", lang, pattern=nick_pattern_filter)
                )
            if content_pattern_filter:
                match_descs.append(
                    _t("clearmsg.desc_content", lang, pattern=content_pattern_filter)
                )
            match_desc = (
                " / ".join(match_descs)
                if match_descs
                else _t("clearmsg.desc_none", lang)
            )
            time_desc = ""
            if has_time_range:
                parts = []
                if start:
                    parts.append(_t("clearmsg.desc_start", lang, value=start))
                if end:
                    parts.append(_t("clearmsg.desc_end", lang, value=end))
                time_desc = ", ".join(parts)
            elif minutes_limit > 0:
                time_desc = _t("clearmsg.desc_within", lang, minutes=minutes_limit)
            return _t(
                "clearmsg.no_match",
                lang,
                match=match_desc,
                time=(time_desc + " ") if time_desc else "",
            )

        success_count = 0
        failed_count = 0
        too_old_count = 0
        channel_map: dict[
            int,
            discord.TextChannel
            | discord.VoiceChannel
            | discord.StageChannel
            | discord.Thread,
        ] = {ch.id: ch for ch in target_channels}
        channel_map.update(scanned_thread_channels)

        # 超过 14 天的消息无法通过 bulk delete 删除 (Discord 限制),
        # 只能逐条 Message.delete() (不受 14 天限制) 删除。
        # 为避免逐条删除触发大量 API 调用/限速, 仅当此类消息总数不超过
        # 配置阈值 (tools.clear-single-delete-max) 时才回退为逐条删除。
        bulk_deletable_seconds = 14 * 86400 - 300

        def is_bulk_deletable(msg: discord.Message) -> bool:
            return (now - msg.created_at).total_seconds() < bulk_deletable_seconds

        single_delete_max = getattr(self.c.tools, "clear_single_delete_max", 0)
        old_message_total = sum(
            1
            for messages in checked_messages_by_channel.values()
            for msg in messages
            if not is_bulk_deletable(msg)
        )
        fallback_single_delete = (
            single_delete_max > 0 and old_message_total <= single_delete_max
        )

        for channel_id, messages in checked_messages_by_channel.items():
            target_channel = channel_map.get(channel_id)
            if target_channel is None:
                failed_count += len(messages)
                continue
            messages.sort(key=lambda m: m.created_at, reverse=True)
            recent_messages = [m for m in messages if is_bulk_deletable(m)]
            old_messages = [m for m in messages if not is_bulk_deletable(m)]

            # 近 14 天内的消息: 使用 bulk delete (每批最多 100 条)
            for i in range(0, len(recent_messages), 100):
                batch = recent_messages[i : i + 100]
                try:
                    if len(batch) == 1:
                        await batch[0].delete()
                        success_count += 1
                    else:
                        await target_channel.delete_messages(batch)
                        success_count += len(batch)
                except discord.Forbidden:
                    return _t("clearmsg.forbidden", lang, channel=target_channel.name)
                except discord.HTTPException as e:
                    if e.code == 50034:
                        too_old_count += len(batch)
                    elif e.code == 10008:
                        pass
                    else:
                        l.error(
                            f"[clear-message] Bulk delete error (channel={target_channel.id}, batch={i}:{i + len(batch)}): {e}"
                        )
                        failed_count += len(batch)
                except Exception as e:
                    l.error(
                        f"[clear-message] Bulk delete error (channel={target_channel.id}): {e}"
                    )
                    failed_count += len(batch)

            # 超过 14 天的消息: 数量较少时回退为逐条删除, 否则计入 too_old
            if old_messages:
                if not fallback_single_delete:
                    too_old_count += len(old_messages)
                    continue
                for msg in old_messages:
                    try:
                        await msg.delete()
                        success_count += 1
                    except discord.Forbidden:
                        return _t(
                            "clearmsg.forbidden", lang, channel=target_channel.name
                        )
                    except discord.NotFound:
                        pass
                    except discord.HTTPException as e:
                        if e.code == 10008:
                            pass
                        else:
                            l.warning(
                                f"[clear-message] Single delete error (channel={target_channel.id}, msg={msg.id}): {e}"
                            )
                            failed_count += 1
                    except Exception as e:
                        l.warning(
                            f"[clear-message] Single delete error (channel={target_channel.id}, msg={msg.id}): {e}"
                        )
                        failed_count += 1

        # ===== 删除整个帖子 (行为 B) =====
        thread_deleted_count = 0
        thread_failed_count = 0
        for th in threads_to_delete:
            try:
                await th.delete()
                thread_deleted_count += 1
            except discord.Forbidden:
                return _t("clearmsg.thread_forbidden", lang, name=th.name)
            except discord.NotFound:
                pass
            except discord.HTTPException as e:
                l.warning(f"[clear-message] Thread delete error (thread={th.id}): {e}")
                thread_failed_count += 1
            except Exception as e:
                l.warning(f"[clear-message] Thread delete error (thread={th.id}): {e}")
                thread_failed_count += 1

        scope_text = _t(
            "clearmsg.scope_channel" if scope == "channel" else "clearmsg.scope_server",
            lang,
        )
        channel_name = getattr(channel, "name", _t("clearmsg.dm_channel", lang))
        channel_info = (
            _t("clearmsg.channel_info", lang, name=channel_name)
            if scope == "channel"
            else ""
        )
        match_descs_result: list[str] = []
        if target_user_ids:
            if len(target_user_ids) == 1 and set(match_types) == {"user"}:
                match_descs_result.append(
                    _t(
                        "clearmsg.result_user",
                        lang,
                        user=user.mention
                        if user
                        else _t("clearmsg.unknown_user", lang),
                    )
                )
            else:
                match_descs_result.append(
                    _t("clearmsg.result_user_ids", lang, ids=sorted(target_user_ids))
                )
        if target_webhook_ids:
            match_descs_result.append(
                _t(
                    "clearmsg.result_webhook_ids",
                    lang,
                    ids=sorted(target_webhook_ids),
                )
            )
        if nick_pattern_filter:
            match_descs_result.append(
                _t("clearmsg.result_nick", lang, pattern=nick_pattern_filter)
            )
        if content_pattern_filter:
            match_descs_result.append(
                _t("clearmsg.result_content", lang, pattern=content_pattern_filter)
            )
        match_desc_result = (
            " / ".join(match_descs_result)
            if match_descs_result
            else _t("clearmsg.result_none", lang)
        )

        time_desc_result: str
        if has_time_range:
            parts = []
            if start:
                parts.append(_t("clearmsg.desc_start", lang, value=start))
            if end:
                parts.append(_t("clearmsg.desc_end", lang, value=end))
            time_desc_result = (
                ", ".join(parts) if parts else _t("clearmsg.result_unlimited", lang)
            )
        elif minutes_limit > 0:
            time_desc_result = _t("clearmsg.result_within", lang, minutes=minutes_limit)
        else:
            time_desc_result = _t("clearmsg.result_unlimited", lang)

        result_lines = [
            _t("clearmsg.result_title", lang),
            _t(
                "clearmsg.result_scope",
                lang,
                scope=scope_text,
                channel_info=channel_info,
            ),
            _t("clearmsg.result_match", lang, match=match_desc_result),
            _t("clearmsg.result_time", lang, time=time_desc_result),
        ]
        if not has_time_range and message_limit > 0:
            result_lines.append(
                _t("clearmsg.result_check_limit", lang, count=message_limit)
            )
        if threads_scanned_count > 0 or delete_threads or target_forums:
            result_lines.append(
                _t(
                    "clearmsg.result_threads_scanned",
                    lang,
                    count=threads_scanned_count,
                )
            )
        result_lines.append(_t("clearmsg.result_matched", lang, count=checked_count))
        result_lines.append(_t("clearmsg.result_deleted", lang, count=success_count))
        if delete_threads:
            result_lines.append(
                _t("clearmsg.result_threads_deleted", lang, count=thread_deleted_count)
            )
        if thread_failed_count > 0:
            result_lines.append(
                _t("clearmsg.result_threads_failed", lang, count=thread_failed_count)
            )
        if too_old_count > 0:
            result_lines.append(
                _t("clearmsg.result_too_old", lang, count=too_old_count)
            )
        if failed_count > 0:
            result_lines.append(_t("clearmsg.result_failed", lang, count=failed_count))
        result_msg = "\n".join(result_lines) + f"\n-# {CLEAR_MESSAGE_MARKER}"

        if self.audit and write_audit:
            audit_detail = (
                _t(
                    "clearmsg.audit_scope",
                    lang,
                    scope=scope_text,
                    channel_info=channel_info,
                )
                + "\n"
                + _t("clearmsg.audit_match", lang, match=match_desc_result)
                + "\n"
                + _t("clearmsg.audit_time", lang, time=time_desc_result)
                + "\n"
                + _t(
                    "clearmsg.audit_summary",
                    lang,
                    checked=checked_count,
                    success=success_count,
                )
                + (
                    _t(
                        "clearmsg.audit_threads",
                        lang,
                        deleted=thread_deleted_count,
                        failed=thread_failed_count,
                    )
                    if delete_threads
                    else ""
                )
                + (
                    _t("clearmsg.audit_too_old", lang, count=too_old_count)
                    if too_old_count > 0
                    else ""
                )
                + (
                    _t("clearmsg.audit_failed", lang, count=failed_count)
                    if failed_count > 0
                    else ""
                )
            )
            await self.audit.log(
                action="clear-message",
                user=author,
                guild=guild,
                channel=channel,
                detail=audit_detail,
                success=failed_count == 0 and thread_failed_count == 0,
            )

        return result_msg
