# Anti-Spam (antispam)

A channel-level anti-spam module based on the `on_message` event, with **no commands**. It configures catch rules for specified channels, automatically kicks / bans / times out the triggering user, and can clean up their recent messages, notify publicly, and write to the audit log.

- **Config key**: `antispam`
- **Source file**: `cogs/antispam.py` (reuses the cleanup capability of `modules/clear_message.py`)

## Decision Flow

For every **non-bot** message in a channel that has rules configured:

1. Ignore bot messages and direct messages.
2. Begin evaluation once the channel's `spam_catcher` rule is hit.
3. If the author has any role in `ignored_roles` → **skip**.
4. Determine the author's category:
   - **Stranger account (spammer)**: has no roles at all, **OR** all of its roles belong to `stranger_roles`.
   - **Normal account (hacked, suspected-compromised)**: all other cases.
5. Execute the corresponding action by category (`spammer` / `hacked`).
6. Optional: clean up the user's recent messages (`clear_message`), notify publicly in the channel (`public_log`).
7. Write the result (success / failure) to the [audit log](/en/modules/audit) (`antispam-auto-catch`, marked as an automatic operation).

## Actions and Required Permissions

| Action | Meaning | Discord permission the bot needs |
| --- | --- | --- |
| `kick` | Kick the member | Kick Members |
| `ban` | Ban the member | Ban Members |
| `mute` / minutes | Timeout (default 60 minutes, or a specified number of minutes) | Moderate Members / Timeout |

::: warning Role hierarchy
If the bot already has the corresponding permission but the operation is still denied, it is almost certainly a **role hierarchy problem**: the target's highest role is not lower than the bot's highest role. In this case, drag the bot's role above the target's. Such failures are recorded to the audit log as "automatic operation failed" with an explanation of the reason.
:::

## Result Notifications

- **Suspected compromised (mute)**: @s the user, indicating the account is suspected to be compromised, has been temporarily muted, and to contact an administrator (in both Chinese and English).
- **Stranger account (kick/ban)**: publicly records the triggered antispam action (in both Chinese and English). Since an @ mention of a member who has been kicked / banned will over time show as "Unknown User", the notification appends `` (`username`) `` after the mention for later identification.
- Whether to notify publicly is controlled by `public_log`.
- A tag like `-# *(antispam-action/{user_id}/{action})*` is appended at the end of the notice for later locating and undoing.

## Message Snapshot (Audit)

When writing to the audit log, the triggering message is **forwarded** to the audit channel and a `Processing...` placeholder message is replied; once cleanup and other processing are complete, that placeholder message is edited into the final "Automated Action Log" (with an undo button, and a fixed-format tag above). The message looks like:

```
-# *(antispam-action/992995849946804304/ban)*
[Automated Action Log embed]
```

- It uses **forwarding** rather than building a snapshot: forwarding keeps a copy of the content in the audit channel, so even if the original message is subsequently cleaned up and deleted, images / attachments will not 404.
- Forwarding must complete **before** the original message is cleaned up and deleted; if forwarding fails, it falls back to building a self-made snapshot embed.

## Undoing Actions (Unban / Unmute)

Mods/Admins can undo antispam actions via the buttons on the audit log embed. The undo flow is **stateless** — it does not rely on a database, only on searching for the tag.

### Unban

1. Click the **Unban** button → the bot first checks if the user is still banned.
2. If already manually unbanned → the button changes to **Expired** (gray, disabled), no further actions.
3. If still banned → performs the unban, the button changes to **{action} by {actor}**.

### Unmute

1. Click the **Unmute** button → the bot first checks if the member is still timed out.
2. If the mute has already expired or been manually removed → the button changes to **Expired** (gray, disabled), no further actions.
3. If still timed out → removes the timeout, the button changes to **{action} by {actor}**.

### Cross-Channel Sync

After undoing, the bot searches all audit log channels (global + per-server) for messages with the same tag and automatically disables their buttons. If `public_log` is enabled, the public notice is also edited, with the format changing to:

```
🚨 Antispam triggered: <@user> (`name`) -> ~~Spammer/ban~~ -> **[Unban by moderator](audit-log-link)**
-# *(antispam-action/xxx/ban)*
```

- The public log's `**Unban by moderator**` does not reveal which specific mod performed the action.
- If `unban_link` is enabled and the server has its own audit channel configured, the text will be a link pointing to the audit log message; otherwise it's plain text.

### Notes

- **Kick** actions cannot be undone — their audit cards have no undo button.
- Errors from inaccessible channels during cross-channel sync are silently ignored.

## Configuration

```yaml
antispam:
  enabled: false
  spam_catcher: {}        # Catch rules configured per channel
  # Example:
  # 1514685631316496615:
  #   spammer: ban              # Stranger handling: kick | ban
  #   hacked: mute              # Suspected-compromised handling: kick | ban | mute | minutes
  #   clear_message: 3          # Auto-cleanup window (minutes, null/false to disable)
  #   public_log: true          # Whether to notify publicly in the channel
  #   unban_link: false         # Whether to append audit log link on undo (requires server audit channel)
  #   stranger_roles: [1318980288046698506, "New Member"]
  #   ignored_roles: ["Admin", "Member"]
```

### Top-level fields

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `enabled` | `bool` | `false` | Whether to enable the anti-spam module |
| `spam_catcher` | `dict[channel ID, rule]` | `{}` | Catch rules configured per channel |

### Per-rule fields

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `spammer` | `kick` / `ban` | `ban` | How to handle stranger accounts |
| `hacked` | `kick` / `ban` / `mute` / minutes(int) | `mute` | How to handle suspected-compromised accounts |
| `clear_message` | `int` / `null` / `false` | `3` | Message cleanup window (minutes); `null`/`false` to disable |
| `public_log` | `bool` | `true` | Whether to notify the result publicly in the channel |
| `unban_link` | `bool` | `false` | Whether to append audit log message link on undo (requires server audit channel configured) |
| `stranger_roles` | `list[int \| str]` | `[]` | List of roles treated as stranger accounts (role ID or name) |
| `ignored_roles` | `list[int \| str]` | `[]` | List of roles to skip processing (having any one is enough to skip) |

- The keys of `spam_catcher` are channel IDs (either numbers or strings).
- Role list items support **role ID** or **role name** (roles with the same name will all be matched).
