# Voice Channel (voice)

Lets the bot join / leave voice channels, with support for DAVE encryption.

- **Config key**: `voicechannel`
- **Source file**: `cogs/voice.py`

## Permission Check (Special)

Voice commands use a **custom permission check** rather than the usual Mod / Admin levels:

- If `allowed_user_ids` is **empty**: only Mods (including Admins) can use them.
- If `allowed_user_ids` is **non-empty**: whitelisted users **or** Mods can use them.

Whitelist entries can be user IDs or usernames.

## Commands

### `vc join` — Join a Voice Channel

Have the bot join the voice channel you are currently in, or a specified channel.

| Item | Description |
| --- | --- |
| Permission | Whitelisted user / Mod |
| Parameters | `channel` (optional, leave empty to use the voice channel you are currently in) |
| Bot permissions | Connect |
| Audit | ✅ Recorded (`joinvc`) |

Behavior details:

- On joining, the bot automatically **self-deaf + self-mute** (self-deafen / self-mute).
- If the bot is already in another channel, it disconnects first and then joins the target channel (showing "Moved to").
- If it is already in the target channel, it shows "Already inside".
- If the channel requires **DAVE encryption** but the connection fails (error code 4017), a corresponding error is shown.
- Stage channels (StageChannel) are not supported for automatic joining as "the channel you are currently in".

### `vc leave` — Leave a Voice Channel

Have the bot leave the voice channel it is currently in.

| Item | Description |
| --- | --- |
| Permission | Whitelisted user / Mod |
| Audit | ✅ Recorded (`leavevc`) |

- If the bot is not in any voice channel, a message is shown.

## Configuration

```yaml
voicechannel:
  enabled: false
  slash: true
  prefix: true
  allowed_user_ids: []    # empty: only mod can use; non-empty: whitelisted users + mod can use
```

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `enabled` | `bool` | `false` | Whether to enable the voice module |
| `slash` | `bool` | `true` | Whether to register slash commands |
| `prefix` | `bool` | `true` | Whether to register prefix commands |
| `allowed_user_ids` | `list[int \| str]` | `[]` | Whitelist for voice commands (user IDs / usernames) |

::: tip DAVE Encryption
DAVE (Discord Audio & Video End-to-End Encryption) depends on `davey` and `PyNaCl`, which are already included in the project dependencies (`discord-py[voice]`).
:::
