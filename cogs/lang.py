import discord
from discord import app_commands
from discord.ext import commands

import utils as u
from i18n import ls
from i18n import t as _t
from lang_store import LangStore
from modules.audit import AuditLogger


class LangCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.c = bot.config  # ty:ignore[unresolved-attribute]
        self.lang_store: LangStore = bot.lang_store  # ty:ignore[unresolved-attribute]
        self.audit: AuditLogger | None = getattr(bot, "audit", None)

    @staticmethod
    def _lang_choices() -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name="zh", value="zh"),
            app_commands.Choice(name="en", value="en"),
        ]

    @staticmethod
    def _scope_choices() -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name="user", value="user"),
            app_commands.Choice(name="server", value="server"),
        ]

    @app_commands.command(name="lang", description=ls("lang.command_description"))
    @app_commands.describe(
        lang=ls("lang.param_lang_description"),
        scope=ls("lang.param_scope_description"),
    )
    @app_commands.choices(lang=_lang_choices(), scope=_scope_choices())
    async def slash_lang(
        self,
        interaction: discord.Interaction,
        lang: str | None = None,
        scope: str = "user",
    ):
        lang_val = lang.strip().lower() if lang else None
        scope_val = scope.strip().lower()

        resolved = self.lang_store.resolve(
            interaction.user.id,
            interaction.guild.id if interaction.guild else None,
        )

        if lang_val is not None and lang_val not in ("zh", "en"):
            await interaction.response.send_message(
                _t("lang.invalid_language", resolved),
                ephemeral=True,
            )
            return

        if scope_val not in ("user", "server"):
            await interaction.response.send_message(
                _t("lang.invalid_scope", resolved),
                ephemeral=True,
            )
            return

        if scope_val == "server":
            if interaction.guild is None:
                await interaction.response.send_message(
                    _t("lang.not_server", resolved),
                    ephemeral=True,
                )
                return
            if not (
                isinstance(interaction.user, discord.Member)
                and interaction.user.guild_permissions.manage_guild
            ) and not u.is_admin(interaction.user, self.c):
                await interaction.response.send_message(
                    _t("lang.server_no_permission", resolved),
                    ephemeral=True,
                )
                return
            if lang_val is None:
                current = self.lang_store.get_guild(interaction.guild.id)
                await interaction.response.send_message(
                    _t("lang.show_server", resolved, lang=current or "zh"),
                    ephemeral=True,
                )
                return
            self.lang_store.set_guild(interaction.guild.id, lang_val)
            await interaction.response.send_message(
                _t("lang.set_server", resolved, lang=lang_val),
                ephemeral=True,
            )
            if self.audit:
                await self.audit.log(
                    action="lang-server",
                    user=interaction.user,
                    guild=interaction.guild,
                    channel=interaction.channel,
                    detail=f"Server language set to `{lang_val}`",
                )
            return

        if lang_val is None:
            current = self.lang_store.get_user(interaction.user.id)
            await interaction.response.send_message(
                _t("lang.show_user", resolved, lang=current or "zh"),
                ephemeral=True,
            )
            return
        self.lang_store.set_user(interaction.user.id, lang_val)
        await interaction.response.send_message(
            _t("lang.set_user", resolved, lang=lang_val),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(LangCog(bot))
