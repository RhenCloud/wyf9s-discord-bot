from loguru import logger as l
import discord
from discord import app_commands
from discord.ext import commands

from modules.audit import AuditLogger
from i18n import t as _t, lang_of, ls
import utils as u


def _voice_permission(
    module: "VoiceCog",
    user: discord.User | discord.Member,
    guild: discord.Guild | None,
) -> bool:
    vc = module.c.voicechannel
    if user.id in vc.allowed_users or user.name in vc.allowed_users:
        return True
    if isinstance(user, discord.Member) and guild is not None:
        guild_users = vc.allowed_guilds.get(
            guild.id, vc.allowed_guilds.get(str(guild.id), [])
        )
        if u.matches_identity(user, guild_users):
            return True
    return u.is_mod(user, module.c, guild)


class VoiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.c = bot.config  # ty:ignore[unresolved-attribute]
        self.audit: AuditLogger | None = getattr(bot, "audit", None)
        self.lang_store = getattr(bot, "lang_store", None)

    def _tr(self, source, key: str, **kwargs) -> str:
        return _t(key, lang_of(source, self.lang_store), **kwargs)

    # ========== Slash Group: /vc ==========

    vc_group = app_commands.Group(name="vc", description=ls("voice.cmd_group_desc"))

    @vc_group.command(name="join", description=ls("voice.cmd_join_desc"))
    @app_commands.describe(channel=ls("voice.param_join_channel"))
    @u.requires(_voice_permission, perm_module="voice")
    async def vc_join(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel | None = None,
    ):
        await self._handle_joinvc(interaction, channel)

    @vc_group.command(name="leave", description=ls("voice.cmd_leave_desc"))
    @u.requires(_voice_permission, perm_module="voice")
    async def vc_leave(self, interaction: discord.Interaction):
        await self._handle_leavevc(interaction)

    # ========== Prefix Group: vc ==========

    @commands.group(name="vc", invoke_without_command=True)
    async def prefix_vc(self, ctx: commands.Context):
        await ctx.send(self._tr(ctx, "voice.usage_prefix"))

    @prefix_vc.command(name="join")
    @u.requires(_voice_permission, perm_module="voice")
    async def prefix_vc_join(
        self, ctx: commands.Context, channel: discord.VoiceChannel | None = None
    ):
        await self._handle_joinvc(ctx, channel)

    @prefix_vc.command(name="leave")
    @u.requires(_voice_permission, perm_module="voice")
    async def prefix_vc_leave(self, ctx: commands.Context):
        await self._handle_leavevc(ctx)

    async def _handle_joinvc(self, source, channel: discord.VoiceChannel | None = None):
        user = source.user if isinstance(source, discord.Interaction) else source.author

        if channel is None and isinstance(user, discord.Member):
            if (
                not user.voice
                or not user.voice.channel
                or isinstance(user.voice.channel, discord.StageChannel)
            ):
                await u.send_msg(
                    source,
                    self._tr(source, "voice.join_first"),
                    ephemeral=True,
                    delete_after=10,
                )
                return
            channel = user.voice.channel

        if not isinstance(channel, discord.VoiceChannel):
            await u.send_msg(
                source,
                self._tr(source, "voice.not_voice_channel"),
                ephemeral=True,
                delete_after=10,
            )
            return

        try:
            guild = source.guild
            if guild and guild.voice_client:
                if (
                    isinstance(guild.voice_client.channel, discord.VoiceChannel)
                    and guild.voice_client.channel.id == channel.id
                ):
                    await u.send_msg(
                        source,
                        self._tr(source, "voice.already_in", channel=channel.name),
                        ephemeral=True,
                        delete_after=10,
                    )
                    return
                await guild.voice_client.disconnect(force=False)
                await channel.connect(self_deaf=True, self_mute=True)
                await u.send_msg(
                    source, self._tr(source, "voice.moved_to", channel=channel.name)
                )
            else:
                await channel.connect(self_deaf=True, self_mute=True)
                await u.send_msg(
                    source, self._tr(source, "voice.joined", channel=channel.name)
                )

            l.info(f"Bot joined voice: {channel.name} ({channel.id})")
            if self.audit:
                await self.audit.log(
                    action="joinvc",
                    user=user,
                    guild=source.guild,
                    channel=source.channel,
                    detail=f"Joined voice `{channel.name}` (`{channel.id}`)",
                )
        except discord.errors.ConnectionClosed as exc:
            if exc.code == 4017:
                await u.send_msg(
                    source,
                    self._tr(source, "voice.dave_failed"),
                    ephemeral=True,
                    delete_after=10,
                )
            else:
                raise
        except discord.ClientException as e:
            await u.send_msg(
                source,
                self._tr(source, "voice.connect_failed", error=e),
                ephemeral=True,
                delete_after=10,
            )
            l.error(f"Failed to join voice: {e}")
        except Exception as e:
            await u.send_msg(
                source,
                self._tr(source, "voice.error_generic", error=type(e).__name__),
                ephemeral=True,
                delete_after=10,
            )
            l.error(f"Unexpected error in joinvc: {type(e).__name__}: {e}")

    async def _handle_leavevc(self, source):
        user = source.user if isinstance(source, discord.Interaction) else source.author

        guild = source.guild
        if not guild or not guild.voice_client:
            await u.send_msg(
                source,
                self._tr(source, "voice.not_in_voice"),
                ephemeral=True,
                delete_after=10,
            )
            return

        if not isinstance(guild.voice_client.channel, discord.VoiceChannel):
            await u.send_msg(
                source,
                self._tr(source, "voice.not_valid_voice"),
                ephemeral=True,
                delete_after=10,
            )
            return

        channel_name = guild.voice_client.channel.name
        await guild.voice_client.disconnect(force=False)
        await u.send_msg(source, self._tr(source, "voice.left", channel=channel_name))

        l.info(f"Bot left voice: {channel_name}")
        if self.audit:
            await self.audit.log(
                action="leavevc",
                user=user,
                guild=source.guild,
                channel=source.channel,
                detail=f"Left voice `{channel_name}`",
            )


async def setup(bot: commands.Bot):
    if bot.config.voicechannel.enabled:  # ty:ignore[unresolved-attribute]
        await bot.add_cog(VoiceCog(bot))
        l.info("VoiceCog loaded.")
