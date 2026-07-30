# Introduction

`wyf9s-discord-bot` is a multi-purpose Discord bot built on [discord.py](https://discordpy.readthedocs.io/), using YAML + [Pydantic v2](https://docs.pydantic.dev/) for config validation and a modular Cog architecture.

## Overall Architecture

```text
config.py            # Pydantic config model + loader
config.yaml          # Runtime configuration (gitignored)
config.example.yaml  # Example config with inline documentation (source of truth for config fields)
schedules.yaml       # Scheduled lock data (auto-generated)
perm.yaml            # Dynamic permission data (managed via the /perm command)
main.py              # Bot entry point, Cog loading, command localization translator registration
utils.py             # Shared utilities (permission checks, rate limiter, message sending, etc.)
perm.py              # Dynamic permission storage service
i18n.py              # i18n runtime (t / lang_of) + Discord command localization translator
lang_store.py        # User / server language preference persistence
lang/                # Language files
  zh.yaml            # Simplified Chinese (default)
  en.yaml            # English
cogs/                # Command modules (discord.py Cog)
  admin.py           # Management commands: /sync, /reload
  emoji.py           # Emoji / sticker commands: /e, /emoji info, /emoji update
  tools.py           # Tools / management commands
  lock.py            # Channel lock / unlock + scheduled locking
  voice.py           # Voice channel commands: /vc join, /vc leave
  antispam.py        # Anti-spam message handling
  manage.py          # Auto-delete event handling
  perm.py            # Dynamic permission commands: /perm add/rm/show
  announce.py        # Announcement following command: /subscribe
  lang.py            # Multilingual command: /lang
modules/             # Shared services (non-Cog)
  audit.py           # Audit log service
  clear_message.py   # Bulk message cleanup service
```

## Startup Flow

The startup process of `main.py` is as follows:

1. **Initialize logging**: First configure [Loguru](https://loguru.readthedocs.io/), and intercept and forward the standard library `logging` (including discord.py's logs) to Loguru.
2. **Load configuration**: `Config()` reads `config.yaml` and validates it with the Pydantic model. A validation failure or a missing file causes an immediate exit.
3. **Reconfigure logging**: Reset the output based on the configured log level, file path, and rotation/retention policy.
4. **Create the client**: `commands.Bot`, with the `message_content` intent enabled and an optional proxy configured. Shared state such as `config`, `audit`, `rate_limiter`, `perm_store`, and `lang_store` is attached to the bot instance.
5. **Register the translator**: `tree.set_translator(I18nTranslator())`, used for localizing slash command / parameter descriptions.
6. **Load Cogs**: Load each Cog module on demand via `load_extension()` (based on the `enabled` switch).
7. **Login**: On `on_ready`, sync slash commands and sync the emoji list.

## Core Design

### Cog Architecture

All command modules use the discord.py **Cog** architecture, supporting hot reload (`/reload`), lifecycle hooks (`cog_load` / `cog_unload`), and event listeners (`@Cog.listener`). Shared state (emoji cache, rate limiter, scheduled lock data, dynamic permissions, etc.) is stored on the `bot` instance and is not lost when reloading a Cog:

```python
class EmojiCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.c = bot.config
        ...

    @app_commands.command(name="e", description="Send an emoji")
    async def e(self, interaction: discord.Interaction, name: str): ...

    # Sub-command group
    emoji_group = app_commands.Group(name="emoji", description="...")

    @emoji_group.command(name="update")
    async def emoji_update(self, interaction: discord.Interaction): ...
```

### Hot Reload

`/reload [module]` can hot-update a single Cog without restarting the bot (with a 15s cooldown to prevent abuse). Without an argument, it lists all available Cogs and their load status. State such as voice connections (VoiceClient), rate limit status, and scheduled lock data is stored on the bot instance and is not lost on reload.

### Declarative Permission Control

Permission checks are unified through the `@u.requires(Permission.MOD)` decorator, supporting two-tier permissions with `config.yaml` + `perm.yaml`. See [Permission System](/en/guide/permissions) for details.

### Multilingual (i18n)

All user-facing text is translated via `lang/zh.yaml` (default) and `lang/en.yaml`, rather than being hardcoded in the code:

- **Runtime messages**: The language is resolved by the priority `user > server > default(zh)` (`i18n.lang_of` + `LangStore.resolve`), and is set with `/lang`.
- **Slash command / parameter descriptions**: Wrapped with `i18n.ls("ns.key")`, registered with English as the base value; `I18nTranslator` provides localization based on the Discord client locale during `tree.sync()` (command names stay unchanged).

See [Multilingual (lang)](/en/modules/lang) for details.

### Dual Command Modes

| Mode | Trigger | Config switch |
| --- | --- | --- |
| Slash command | `/random` | Each module's `slash: true` |
| Prefix command | `//vc leave` (prefix determined by `command_prefix`) | Each module's `prefix: true` |

::: tip About prefix command arguments
Slash commands pass arguments through Discord's native parameter UI; some complex prefix commands (such as `clear-message` and `lock plan`) pass arguments using `--key=value` flags (quoted values are supported).
:::

## Tech Stack

| Component | Description |
| --- | --- |
| **Python 3.13** | Runtime (`pyproject.toml` requires `>=3.10`) |
| **[uv](https://docs.astral.sh/uv/)** | Package manager |
| **[discord.py](https://discordpy.readthedocs.io/)** `[voice]` | Discord API wrapper (Cog architecture) |
| **[Pydantic v2](https://docs.pydantic.dev/)** | Config model validation |
| **[PyYAML](https://pyyaml.org/)** | Config file parsing |
| **[Loguru](https://loguru.readthedocs.io/)** | Logging |
| **[aiohttp](https://docs.aiohttp.org/)** | Async HTTP requests |
| **davey / PyNaCl** | Voice / DAVE encryption support |

## Module Overview

| Module | Key | Type | Description |
| --- | --- | --- | --- |
| [Tools / Management](/en/modules/tools) | `tools` | Command | Random number / UUID, message deletion, bulk cleanup, channel moving, text-to-file |
| [Emoji](/en/modules/emoji) | `emoji` | Command | Browse / search / send remote emoji packs |
| [Channel Lock](/en/modules/lock) | `lock` | Command | Lock / unlock channels, scheduled locking |
| [Voice Channel](/en/modules/voice) | `voicechannel` | Command | Join / leave voice channels |
| [Management Commands](/en/modules/admin) | — | Command | Command sync `/sync`, hot reload `/reload` |
| [Dynamic Permissions](/en/modules/perm) | `perm` | Command | `/perm add/rm/show` permission rule management |
| [Announcement Following](/en/modules/announce) | `announce` | Command | `/subscribe` to follow announcement channels |
| [Multilingual](/en/modules/lang) | — (always enabled) | Command | `/lang` to switch user / server language |
| [Auto Management](/en/modules/manage) | `rmmsg` / `rmtodo` | Event | Auto-delete messages |
| [Anti-Spam](/en/modules/antispam) | `antispam` | Event | Channel-level anti-spam rules |
| [Audit Log](/en/modules/audit) | `audit` | Service | Record management actions to a specified channel |

> For each module's detailed commands, usage, and required permissions, see the [Module Overview](/en/modules/).
