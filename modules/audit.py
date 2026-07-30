from datetime import datetime, timezone

from loguru import logger as l
import discord
from discord.ext import commands

from config import ConfigModel
from i18n import t as _t
from lang_store import LangStore


class AntispamActionView(discord.ui.View):
    def __init__(
        self,
        *,
        guild_id: int,
        target_id: int,
        target_name: str,
        action_label: str,
        lang: str,
        lang_store: LangStore,
    ):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.target_id = target_id
        self.target_name = target_name
        self.action_label = action_label
        self.lang = lang
        self.lang_store = lang_store

        if "ban" in action_label.lower():
            self._add_btn_unban()
        elif "mute" in action_label.lower():
            self._add_btn_unmute()

    def _add_btn_unban(self):
        btn = discord.ui.Button(
            label=_t("antispam.snapshot_button_unban", self.lang),
            style=discord.ButtonStyle.danger,
            custom_id=f"antispam:unban:{self.guild_id}:{self.target_id}",
        )
        btn.callback = self._handle_unban  # ty:ignore[invalid-assignment]
        self.add_item(btn)

    def _add_btn_unmute(self):
        btn = discord.ui.Button(
            label=_t("antispam.snapshot_button_unmute", self.lang),
            style=discord.ButtonStyle.danger,
            custom_id=f"antispam:unmute:{self.guild_id}:{self.target_id}",
        )
        btn.callback = self._handle_unmute  # ty:ignore[invalid-assignment]
        self.add_item(btn)

    async def _resolve_lang(self, interaction: discord.Interaction) -> str:
        return self.lang_store.resolve(
            interaction.user.id, interaction.guild.id if interaction.guild else None
        )

    async def _finalize_button(
        self, interaction: discord.Interaction, action_key: str
    ) -> None:
        """
        成功后: 禁用按钮并改为 '已由 {moderator} {action}', 更新原消息

        标签属于审计日志消息的一部分, 因此使用服务器语言 (self.lang) 而非操作者语言
        """
        actor = getattr(interaction.user, "display_name", str(interaction.user))
        action_label = _t(action_key, self.lang)
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
                item.label = _t(
                    "antispam.snapshot_button_done",
                    self.lang,
                    actor=actor,
                    action=action_label,
                )[:80]
        self.stop()
        try:
            await interaction.response.edit_message(view=self)
        except discord.HTTPException:
            pass

    async def _handle_unban(self, interaction: discord.Interaction):
        lang = await self._resolve_lang(interaction)
        guild = interaction.client.get_guild(self.guild_id)
        if guild is None:
            await interaction.response.send_message(
                _t("antispam.guild_not_found", lang), ephemeral=True
            )
            return
        if not (
            isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.ban_members
        ):
            await interaction.response.send_message(
                _t("antispam.snapshot_button_no_permission", lang),
                ephemeral=True,
            )
            return
        try:
            user = await interaction.client.fetch_user(self.target_id)
            await guild.unban(
                user,
                reason=_t("antispam.unban_reason", lang, actor=str(interaction.user)),
            )
            await self._finalize_button(interaction, "antispam.action_unban")
            # Broadcast antispam action tag to all audit channels
            audit = interaction.client.audit  # ty:ignore[unresolved-attribute]
            if audit:
                await audit._broadcast_antispam_tag(guild, self.target_id, "unban")
        except discord.NotFound:
            await interaction.response.send_message(
                _t("antispam.user_not_found_ban", lang), ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                _t("antispam.snapshot_button_no_permission", lang),
                ephemeral=True,
            )
        except Exception as e:
            await interaction.response.send_message(
                _t("antispam.snapshot_button_failed", lang, error=str(e)[:400]),
                ephemeral=True,
            )

    async def _handle_unmute(self, interaction: discord.Interaction):
        lang = await self._resolve_lang(interaction)
        guild = interaction.client.get_guild(self.guild_id)
        if guild is None:
            await interaction.response.send_message(
                _t("antispam.guild_not_found", lang), ephemeral=True
            )
            return
        if not (
            isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.moderate_members
        ):
            await interaction.response.send_message(
                _t("antispam.snapshot_button_no_permission", lang),
                ephemeral=True,
            )
            return
        try:
            member = await guild.fetch_member(self.target_id)
            await member.timeout(
                None,
                reason=_t("antispam.unmute_reason", lang, actor=str(interaction.user)),
            )
            await self._finalize_button(interaction, "antispam.action_unmute")
            # Broadcast antispam action tag to all audit channels
            audit = interaction.client.audit  # ty:ignore[unresolved-attribute]
            if audit:
                await audit._broadcast_antispam_tag(guild, self.target_id, "unmute")
        except discord.NotFound:
            await interaction.response.send_message(
                _t("antispam.member_not_found", lang), ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                _t("antispam.snapshot_button_no_permission", lang),
                ephemeral=True,
            )
        except Exception as e:
            await interaction.response.send_message(
                _t("antispam.snapshot_button_failed", lang, error=str(e)[:400]),
                ephemeral=True,
            )


class AuditLogger:
    c: ConfigModel
    client: commands.Bot
    lang_store: LangStore

    def __init__(
        self, config: ConfigModel, client: commands.Bot, lang_store: LangStore
    ):
        self.c = config
        self.client = client
        self.lang_store = lang_store

    def _resolve_targets(self, guild: discord.Guild | None) -> list[int]:
        targets: list[int] = []
        seen: set[int] = set()

        if self.c.audit.global_channel:
            targets.append(self.c.audit.global_channel)
            seen.add(self.c.audit.global_channel)

        if guild is not None:
            guild_conf = self.c.audit.guilds.get(
                guild.id, self.c.audit.guilds.get(str(guild.id))
            )
            if guild_conf is not None and guild_conf.channel not in seen:
                targets.append(guild_conf.channel)

        return targets

    def _resolve_lang(self, guild: discord.Guild | None) -> str:
        if guild is not None:
            return self.lang_store.resolve(0, guild.id)
        return "zh"

    def _build_embed(
        self,
        *,
        lang: str,
        action: str,
        user: discord.User | discord.Member,
        guild: discord.Guild | None,
        channel: discord.abc.GuildChannel
        | discord.abc.PrivateChannel
        | discord.Thread
        | None,
        detail: str,
        success: bool,
        auto: bool,
    ) -> discord.Embed:
        color = discord.Color.green() if success else discord.Color.red()

        if auto:
            title_key = "audit.title_auto_ok" if success else "audit.title_auto_fail"
        else:
            title_key = (
                "audit.title_manual_ok" if success else "audit.title_manual_fail"
            )

        embed = discord.Embed(
            title=_t(title_key, lang),
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name=_t("audit.field_action", lang),
            value=f"`{action}`",
            inline=True,
        )
        embed.add_field(
            name=_t("audit.field_actor", lang),
            value=f"{user.mention} (`{user.name}` / `{user.id}`)",
            inline=True,
        )
        if guild is not None:
            embed.add_field(
                name=_t("audit.field_guild", lang),
                value=f"`{guild.name}` (`{guild.id}`)",
                inline=False,
            )
        if channel is not None:
            channel_repr = getattr(channel, "mention", None) or getattr(
                channel, "name", str(channel)
            )
            embed.add_field(
                name=_t("audit.field_channel", lang),
                value=str(channel_repr),
                inline=False,
            )
        if detail:
            embed.add_field(
                name=_t("audit.field_detail", lang),
                value=detail[:1024],
                inline=False,
            )
        return embed

    async def _send_to_channel(
        self,
        channel_id: int,
        *,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
        content: str | None = None,
    ):
        try:
            target = self.client.get_channel(channel_id)
            if target is None:
                target = await self.client.fetch_channel(channel_id)
            if not isinstance(target, (discord.TextChannel, discord.Thread)):
                l.warning(
                    f"[audit] Log channel {channel_id} is not a text channel, skipped"
                )
                return None
            kwargs: dict[str, discord.Embed | discord.ui.View | str] = {}
            if embed:
                kwargs["embed"] = embed
            if view:
                kwargs["view"] = view
            if content:
                kwargs["content"] = content
            return await target.send(**kwargs)  # ty:ignore[no-matching-overload]
        except discord.Forbidden:
            l.warning(f"[audit] No permission to send to log channel {channel_id}")
        except discord.NotFound:
            l.warning(f"[audit] Log channel {channel_id} not found")
        except Exception as e:
            l.warning(f"[audit] Error sending log to channel {channel_id}: {e}")
        return None

    async def log(
        self,
        *,
        action: str,
        user: discord.User | discord.Member,
        guild: discord.Guild | None = None,
        channel: discord.abc.GuildChannel
        | discord.abc.PrivateChannel
        | discord.Thread
        | None = None,
        detail: str = "",
        success: bool = True,
        auto: bool = False,
    ):
        if not self.c.audit.enabled:
            return

        targets = self._resolve_targets(guild)
        if not targets:
            return

        lang = self._resolve_lang(guild)
        embed = self._build_embed(
            lang=lang,
            action=action,
            user=user,
            guild=guild,
            channel=channel,
            detail=detail,
            success=success,
            auto=auto,
        )

        for channel_id in targets:
            await self._send_to_channel(channel_id, embed=embed)

    async def _broadcast_antispam_tag(
        self, guild: discord.Guild, user_id: int, action: str
    ) -> None:
        """Send a stateless tag message to all audit channels.
        The format is ``-# *(antispam-action/<user_id>/<action>)*``.
        If ``unban_link`` is enabled in the antispam rule, a message link can be
        appended (handled by the caller).
        """
        if not self.c.audit.enabled:
            return
        targets = self._resolve_targets(guild)
        if not targets:
            return
        content = f"-# *(antispam-action/{user_id}/{action})*"
        for cid in targets:
            await self._send_to_channel(cid, content=content)

    @staticmethod
    def _build_message_snapshot_embed(
        message: discord.Message,
        lang: str,
    ) -> discord.Embed:
        embed = discord.Embed(
            title=_t("antispam.snapshot_title", lang),
            color=discord.Color.blue(),
            timestamp=message.created_at,
        )

        content = message.content or ""
        if not content and message.embeds:
            content = _t("audit.snapshot_content_embed", lang)
        if not content and message.attachments:
            content = _t("audit.snapshot_content_attachment", lang)
        if not content:
            content = _t("antispam.snapshot_content_empty", lang)

        embed.add_field(
            name=_t("audit.snapshot_field_content", lang),
            value=content[:1024] if len(content) <= 1024 else content[:1021] + "...",
            inline=False,
        )

        embed.add_field(
            name=_t("audit.snapshot_field_author", lang),
            value=f"{message.author} (`{message.author.id}`)",
            inline=True,
        )
        embed.add_field(
            name=_t("antispam.snapshot_field_channel", lang),
            value=f"{message.channel} (`{message.channel.id}`)",
            inline=True,
        )

        attachment_urls = [a.url for a in message.attachments]
        sticker_urls = [s.url for s in message.stickers if hasattr(s, "url") and s.url]
        all_urls = attachment_urls + sticker_urls
        if all_urls:
            embed.add_field(
                name=_t("antispam.snapshot_field_attachments", lang),
                value="\n".join(all_urls)[:1024],
                inline=False,
            )
        else:
            embed.add_field(
                name=_t("antispam.snapshot_field_attachments", lang),
                value=_t("antispam.snapshot_field_no_attachments", lang),
                inline=False,
            )

        if message.jump_url:
            embed.add_field(
                name=_t("audit.snapshot_field_jump", lang),
                value=message.jump_url,
                inline=False,
            )

        if message.author.avatar:
            embed.set_thumbnail(url=message.author.avatar.url)

        embed.set_footer(text=_t("audit.snapshot_footer", lang, id=message.id))
        return embed

    async def forward_antispam_trigger(
        self,
        *,
        guild: discord.Guild,
        trigger_message: discord.Message,
    ) -> dict[int, discord.Message]:
        """
        将触发消息转发到各审计频道, 并回复一个 "处理中..." 占位消息。

        必须在清理 (cleanup) 删除原消息之前调用: 转发会创建一份内容快照,
        即使原消息随后被删除, 转发副本 (含图片/附件) 依然保留, 避免自建
        snapshot 在原消息删除后图片链接 404 的问题。

        返回 {channel_id: 占位消息}, 供后续 log_antispam_with_snapshot 编辑。
        """
        if not self.c.audit.enabled:
            return {}

        targets = self._resolve_targets(guild)
        if not targets:
            return {}

        lang = self._resolve_lang(guild)
        pending: dict[int, discord.Message] = {}

        for channel_id in targets:
            target = self.client.get_channel(channel_id)
            if target is None:
                try:
                    target = await self.client.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    continue
            if not isinstance(target, (discord.TextChannel, discord.Thread)):
                l.warning(
                    f"[audit] Log channel {channel_id} is not a text channel, skipped"
                )
                continue
            try:
                forwarded = await trigger_message.forward(target)
                pending[channel_id] = await forwarded.reply(
                    _t("audit.processing", lang)
                )
            except discord.HTTPException as e:
                l.warning(
                    f"[audit] Failed to forward trigger message to {channel_id}: {e}"
                )

        return pending

    async def log_antispam_with_snapshot(
        self,
        *,
        user: discord.User | discord.Member,
        guild: discord.Guild,
        channel: discord.TextChannel,
        detail: str,
        success: bool,
        trigger_message: discord.Message,
        category: str,
        action_label: str,
        pending: dict[int, discord.Message] | None = None,
    ):
        if not self.c.audit.enabled:
            return

        targets = self._resolve_targets(guild)
        if not targets:
            return

        lang = self._resolve_lang(guild)

        embed = self._build_embed(
            lang=lang,
            action="antispam-auto-catch",
            user=user,
            guild=guild,
            channel=channel,
            detail=detail,
            success=success,
            auto=True,
        )

        pending = pending or {}

        for channel_id in targets:
            view = AntispamActionView(
                guild_id=guild.id,
                target_id=user.id,
                target_name=str(user),
                action_label=action_label,
                lang=lang,
                lang_store=self.lang_store,
            )
            placeholder = pending.get(channel_id)
            if placeholder is not None:
                # 转发成功: 编辑占位 "处理中..." 消息为最终操作日志
                try:
                    await placeholder.edit(content=None, embed=embed, view=view)
                    continue
                except discord.HTTPException as e:
                    l.warning(
                        f"[audit] Failed to edit placeholder in {channel_id}: {e}"
                    )
            # 转发/占位失败时回退: 自建 snapshot embed + 操作日志
            snapshot_embed = self._build_message_snapshot_embed(trigger_message, lang)
            await self._send_to_channel(channel_id, embed=snapshot_embed)
            await self._send_to_channel(channel_id, embed=embed, view=view)
