import time

import discord
from discord import app_commands
from discord.ext import commands
from loguru import logger as l

import utils as u
from i18n import lang_of, ls
from i18n import t as _t
from modules.audit import AuditLogger

# Cooldown for /reload to prevent spam: per-user timestamps
_reload_cooldowns: dict[int, float] = {}
_RELOAD_COOLDOWN = 15  # seconds


# Special reload target: reload config.yaml (instead of a cog)
_CONFIG_TARGET = "config"


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.c = bot.config  # ty:ignore[unresolved-attribute]
        self.audit: AuditLogger | None = getattr(bot, "audit", None)
        self.lang_store = getattr(bot, "lang_store", None)

    def _tr(self, source, key: str, **kwargs) -> str:
        return _t(key, lang_of(source, self.lang_store), **kwargs)

    def _list_cogs(self) -> list[str]:
        """列出 cogs 目录下的模块名 (缓存 1 秒)"""
        return u.list_cog_names()

    async def reload_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        options = [_CONFIG_TARGET, *self._list_cogs()]
        cur = current.lower()
        return [
            app_commands.Choice(name=name, value=name)
            for name in options
            if cur in name.lower()
        ][:25]

    # ========== /sync ==========

    @app_commands.command(name="sync", description=ls("admin.cmd_sync_desc"))
    @u.requires(u.Permission.ADMIN)
    async def slash_sync(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.bot.tree.sync()
        l.info("Command tree synced.")
        await interaction.followup.send(self._tr(interaction, "admin.synced"))
        if self.audit:
            await self.audit.log(
                action="sync",
                user=interaction.user,
                guild=interaction.guild,
                channel=interaction.channel,
                detail="Synced slash command tree",
            )

    @commands.command(name="sync")
    @u.requires(u.Permission.ADMIN)
    async def prefix_sync(self, ctx: commands.Context):
        await ctx.defer()
        await self.bot.tree.sync()
        await ctx.send(self._tr(ctx, "admin.synced"))
        if self.audit:
            await self.audit.log(
                action="sync",
                user=ctx.author,
                guild=ctx.guild,
                channel=ctx.channel,  # ty:ignore[invalid-argument-type]
                detail="Synced slash command tree",
            )

    @commands.command(name="sync-commands")
    @u.requires(u.Permission.ADMIN)
    async def prefix_sync_commands(self, ctx: commands.Context):
        await ctx.defer()
        await self.bot.tree.sync()
        await ctx.send(self._tr(ctx, "admin.synced"))
        if self.audit:
            await self.audit.log(
                action="sync-commands",
                user=ctx.author,
                guild=ctx.guild,
                channel=ctx.channel,  # ty:ignore[invalid-argument-type]
                detail="Synced slash command tree",
            )

    # ========== /reload ==========

    @app_commands.command(name="reload", description=ls("admin.cmd_reload_desc"))
    @app_commands.describe(module=ls("admin.param_reload_module"))
    @app_commands.autocomplete(module=reload_autocomplete)
    @u.requires(u.Permission.ADMIN)
    async def slash_reload(
        self, interaction: discord.Interaction, module: str | None = None
    ):
        now = time.monotonic()
        uid = interaction.user.id
        if uid in _reload_cooldowns:
            elapsed = now - _reload_cooldowns[uid]
            if elapsed < _RELOAD_COOLDOWN:
                remaining = _RELOAD_COOLDOWN - elapsed
                await interaction.response.send_message(
                    self._tr(
                        interaction,
                        "admin.reload_cooldown",
                        remaining=f"{remaining:.0f}",
                    ),
                    ephemeral=True,
                )
                return
        _reload_cooldowns[uid] = now

        await self._handle_reload(interaction, module)

    @commands.command(name="reload")
    @u.requires(u.Permission.ADMIN)
    async def prefix_reload(self, ctx: commands.Context, *, module: str | None = None):
        now = time.monotonic()
        uid = ctx.author.id
        if uid in _reload_cooldowns:
            elapsed = now - _reload_cooldowns[uid]
            if elapsed < _RELOAD_COOLDOWN:
                remaining = _RELOAD_COOLDOWN - elapsed
                await ctx.send(
                    self._tr(
                        ctx, "admin.reload_cooldown_short", remaining=f"{remaining:.0f}"
                    ),
                    delete_after=10,
                )
                return
        _reload_cooldowns[uid] = now

        await self._handle_reload(ctx, module)

    async def _reply_reload(self, source, msg: str, *, error: bool = False):
        is_interaction = isinstance(source, discord.Interaction)
        if is_interaction:
            await source.followup.send(msg, ephemeral=error)
        else:
            await source.send(msg, delete_after=10 if error else None)

    async def _handle_reload(self, source, module: str | None):
        is_interaction = isinstance(source, discord.Interaction)
        user = source.user if is_interaction else source.author

        if is_interaction:
            await source.response.defer()

        target = module.lower().strip() if module else None

        # 1) config reload
        if target == _CONFIG_TARGET:
            await self._handle_reload_config(source, user)
            return

        # 2) reload all loaded cogs (no arg)
        if target is None:
            await self._handle_reload_all(source, user)
            return

        # 3) reload a single cog
        available = self._list_cogs()
        ext_name = f"cogs.{target}"
        if ext_name not in self.bot.extensions and target not in available:
            await self._reply_reload(
                source,
                self._tr(source, "admin.cog_not_found", module=target),
                error=True,
            )
            return

        try:
            if ext_name in self.bot.extensions:
                await self.bot.reload_extension(ext_name)
            else:
                await self.bot.load_extension(ext_name)

            if target == "perm":
                perm_store = getattr(self.bot, "perm_store", None)
                if perm_store:
                    perm_store._load()

            l.info(f"Reloaded cog: {target}")
            await self._reply_reload(
                source, self._tr(source, "admin.reloaded", module=target)
            )
            if self.audit:
                await self.audit.log(
                    action="reload",
                    user=user,
                    guild=source.guild,
                    channel=source.channel,
                    detail=f"Reloaded cog: {target}",
                )
        except Exception as e:
            l.error(f"Failed to reload cog {target}: {e}")
            await self._reply_reload(
                source,
                self._tr(source, "admin.reload_failed", module=target, error=e),
                error=True,
            )

    async def _reload_all_extensions(self) -> tuple[int, list[str]]:
        """重载所有已加载的 cog, 返回 (成功数, 失败信息列表)"""
        succeeded = 0
        failures: list[str] = []
        for ext_name in list(self.bot.extensions):
            try:
                await self.bot.reload_extension(ext_name)
                succeeded += 1
            except Exception as e:
                failures.append(f"`{ext_name}`: {e}")
                l.error(f"Failed to reload {ext_name}: {e}")
        perm_store = getattr(self.bot, "perm_store", None)
        if perm_store:
            perm_store._load()
        return succeeded, failures

    async def _handle_reload_all(self, source, user):
        succeeded, failures = await self._reload_all_extensions()
        l.info(f"Reloaded all cogs: {succeeded} ok, {len(failures)} failed")
        if failures:
            msg = (
                self._tr(
                    source,
                    "admin.reload_all_partial",
                    count=succeeded,
                    failed=len(failures),
                )
                + "\n"
                + "\n".join(f"  {f}" for f in failures)
            )
        else:
            msg = self._tr(source, "admin.reload_all", count=succeeded)
        await self._reply_reload(source, msg, error=bool(failures))
        if self.audit:
            await self.audit.log(
                action="reload-all",
                user=user,
                guild=source.guild,
                channel=source.channel,
                detail=f"Reloaded {succeeded} cogs, {len(failures)} failed",
                success=not failures,
            )

    async def _handle_reload_config(self, source, user):
        from config import Config

        try:
            cfg_instance = getattr(self.bot, "_config_instance", None)
            if cfg_instance is not None:
                new_config = Config(
                    config_path=cfg_instance._config_path,
                    token_file=cfg_instance._token_file,
                    token=cfg_instance._token,
                ).config
            else:
                new_config = Config().config
        except Exception as e:
            l.error(f"Failed to reload config: {e}")
            await self._reply_reload(
                source,
                self._tr(source, "admin.config_reload_failed", error=e),
                error=True,
            )
            return

        # Update shared config references, then reload cogs so they pick it up
        self.bot.config = new_config  # ty:ignore[unresolved-attribute]
        self.c = new_config
        if self.audit:
            self.audit.c = new_config
        succeeded, failures = await self._reload_all_extensions()
        l.info(f"Config reloaded; {succeeded} cogs reloaded, {len(failures)} failed")

        msg = self._tr(source, "admin.config_reloaded", count=succeeded)
        if failures:
            msg += "\n" + "\n".join(f"  {f}" for f in failures)
        await self._reply_reload(source, msg, error=bool(failures))
        if self.audit:
            await self.audit.log(
                action="reload-config",
                user=user,
                guild=source.guild,
                channel=source.channel,
                detail=f"Reloaded config + {succeeded} cogs, {len(failures)} failed",
                success=not failures,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
    l.info("AdminCog loaded.")
