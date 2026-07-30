import discord
from discord import app_commands
from discord.ext import commands
from loguru import logger as l

import utils as u
from i18n import lang_of, ls
from i18n import t as _t
from modules.audit import AuditLogger


class AnnounceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.c = bot.config  # ty:ignore[unresolved-attribute]
        self.audit: AuditLogger | None = getattr(bot, "audit", None)
        self.lang_store = getattr(bot, "lang_store", None)

    def _tr(self, source, key: str, **kwargs) -> str:
        return _t(key, lang_of(source, self.lang_store), **kwargs)

    # ========== /subscribe ==========

    @app_commands.command(
        name="subscribe",
        description=ls("announce.cmd_desc"),
    )
    @app_commands.describe(target=ls("announce.param_target"))
    @u.requires(u.Permission.MOD)
    async def subscribe(
        self,
        interaction: discord.Interaction,
        target: discord.TextChannel | None = None,
    ):
        source_id = self.c.announce.source_channel
        if not source_id:
            await interaction.response.send_message(
                self._tr(interaction, "announce.not_configured"), ephemeral=True
            )
            return

        destination = target or interaction.channel
        if not isinstance(destination, discord.TextChannel):
            await interaction.response.send_message(
                self._tr(interaction, "announce.must_text_channel"), ephemeral=True
            )
            return

        if not interaction.guild:
            await interaction.response.send_message(
                self._tr(interaction, "announce.server_only"), ephemeral=True
            )
            return

        source = self.bot.get_channel(source_id)
        if source is None:
            try:
                source = await self.bot.fetch_channel(source_id)
            except (discord.NotFound, discord.Forbidden):
                await interaction.response.send_message(
                    self._tr(interaction, "announce.channel_not_found"), ephemeral=True
                )
                return

        if not isinstance(source, discord.TextChannel):
            await interaction.response.send_message(
                self._tr(interaction, "announce.source_not_text"),
                ephemeral=True,
            )
            return

        if not source.is_news():
            await interaction.response.send_message(
                self._tr(interaction, "announce.source_not_news"),
                ephemeral=True,
            )
            return

        try:
            # follow() must be called on the news/source channel; `destination`
            # is the target channel that will receive the announcements.
            await source.follow(
                destination=destination,
                reason=f"Subscribed by {interaction.user} via wyf9-bot",
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                self._tr(interaction, "announce.need_manage_webhooks"),
                ephemeral=True,
            )
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(
                self._tr(interaction, "announce.follow_failed", error=e),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            self._tr(interaction, "announce.success", channel=destination.mention)
        )

        if self.audit:
            await self.audit.log(
                action="subscribe",
                user=interaction.user,
                guild=interaction.guild,
                channel=interaction.channel,
                detail=f"Followed {destination.name} ({destination.id}) from announcement channel",
            )


async def setup(bot: commands.Bot):
    announce_cfg = getattr(bot.config, "announce", None)  # ty:ignore[unresolved-attribute]
    if announce_cfg and announce_cfg.source_channel:
        await bot.add_cog(AnnounceCog(bot))
        l.info("AnnounceCog loaded.")
