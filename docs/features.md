# Extensive Feature list
Here's complete list of everything the tool can handle and any specific caveats or workarounds.

## 🟢 Fluxer sources are supported for direct migration
Starting with V4, you can migrate **from** a Fluxer community (official or self-hosted) **to** another Fluxer or Stoat community. Roles, channels, permissions, emojis, icons, messages, and more — all supported*. See the [guide](guide.md) for setup.

*Barring Sticker Cloning — stickers are not cloned from Fluxer sources yet, and cloning stickers **to** a self-hosted Fluxer target fails (`401 UNAUTHORIZED` — an upstream `fluxer.py` issue; use the official Fluxer instance for stickers).

- 🟩Fully supported
- 🟧Work Around implemented
- 🟥Limitation

---

# Migration Tool

## Server Identity
* 🟩 **Server Name**
* 🟩 **Server Icon**
* 🟩 **Server Banner**

## Server Structure
* 🟩 **Categories**
    - 🟩 **Metadata**: Copies Category names, also preserve the position of the Categories and its child channels.
    - 🟩 **Permission Overwrites**: Copies role-specific permission overwrites set at the category level.
* 🟩 **Channels**
    - 🟩 **Metadata**: Copies Channel names, topics/descriptions.
    - 🟩 **NSFW Flag** is preserved for age restricted channels.
    - 🟧 **Slowmode** timing rules are preserved for text channels (Stoat doesnt have slowmode).
    - 🟩 **Bitrate** setting is also preserved Voice channels
    - 🟩 **Permission Overwrites**: Copies role-specific permission overwrites set on individual channels.

## Roles
*  🟩 **Role Cloning**: Clones every custom role with its original name, color (hex code), and hierarchical position.
*  🟩 **Role Settings**: Copies the "Display role members separately from online members" (hoist) and "Allow anyone to @mention this role" settings.

### Text Messages
* 🟩 **Markdown Support**: Preserves all standard and advanced Discord markdown:
    - `**Bold**`, `*Italics*`, `__Underline__`, `~~Strikethrough~~`.
    - `> Blockquotes` and `>>> Multi-line quotes`.
    - `Code blocks` and ` ```Full code blocks with syntax highlighting``` `.
    - `||Spoilers||`.
* 🟩 **Message Authors**: Preserves the original author's name and avatar in migrated messages.
* 🟩 **Reply Links**: Preserves the connection of replies if the referenced message was previously migrated.
* 🟩 **Embeds**: Clones rich embeds sent by bots or webhooks, including their colors, fields, and descriptions.

### File Attachments
* 🟩 **Media**: Downloads and re-uploads images, videos, and audio clips.
* 🟩 **Documents**: Preserves PDFs, text files, and other message attachments.
* 🟧 **Size Limits**: Automatically checks size limits. If an attachment is too large for the target platform.
> Stoat doesnt support many file types, so those attachments are skipped and an error message will be sent in place of the attachment

### Emojis
* 🟩 **Standard Emojis**: Full support for Unicode emojis.
* 🟩 **Custom Server Emojis**: Automatically clones custom server emojis (both static and animated) to the target server.
* 🟩 **Oversized Emojis**: Emojis over the target's size limit (e.g. 512 KB on Fluxer) are automatically resized/compressed before upload, so one large emoji can't stall the migration.
* 🟩 **Emoji Mapping in messages**: References to custom emojis in migrated messages are updated to point to the new emoji IDs on the target platform.
* 🟥 **External Emojis**: Emojis from external servers are not migrated; only the `:emoji_name:` will be displayed.

### Stickers
* 🟧 **Normal Stickers**: Migrates static and animated stickers (`.png`, `.apng`, `.gif`).
* 🟧 **Lottie Stickers**: Converted during migration:
    - **Fluxer**: Converted to `.webp` for compatibility.
    - **Stoat**: Converted to `.gif`.
> Stickers are currently migrated as attachments since sending stickers via api is not yet fully supported on target platforms.
> ⚠️ **Self-hosted Fluxer targets**: sticker cloning fails with `401 UNAUTHORIZED` even for Administrator bots (upstream `fluxer.py` bug) — use the official Fluxer instance for stickers. Stickers are also not cloned when the **source** is Fluxer.

### Mentions & Discord Links
* 🟩 **Channel Mentions**: Automatically resolves `#channel` mentions to point to the correct migrated channel on the target server.
* 🟩 **Role Mentions**: Automatically resolves `@role` mentions to point to the cloned role on the target platform.
* 🟩 **User Mentions**: Mentions of users are preserved as text [`@nickname`] if that user.
* 🟩 **Message Links**: Standard Discord message URLs are resolved to the new URL on Fluxer or Stoat (as long as the target message has been migrated).
* 🟩 **Channel Links**: Discord channel URLs (like `discord.com/channels/...`) are resolved to their matching target channel link.

### Threads & Forums
* 🟩 **Active & Archived Threads**: Scans and migrates messages from all threads.
* 🟧 **Thread Structure**: Maintains the nesting of threads within their parent channels.
> This is a workaround as Threads and Forums are not yet implement in Fluxer and Stoat

### Misc features
* 🟩 **Incremental Sync**: Scans migration database to find the "Last Message ID." It skips duplicate data and continues where it left off.
* 🟩 **Audit Logging**: Creates a `#reaper-logs` channel in the target community and sends status updates and error reports.

---

# Backup Tool
You can create a local snapshot of your Discord server using the Backup Tool.
> ⚠️ Backups currently support **Discord sources only** — backing up Fluxer communities is planned but not implemented yet.

* 🟩 **SQLite Database**: All server mappings, message IDs, and content are stored in a SQLite database.
* 🟩 **Deduplicated Attachments**: All the attachment media is stored in a content-addressable storage system.
* 🟩 **Incremental Syncing**: Scans your existing local files and only downloads messages that have been sent since your last backup.
* 🟩 **Supported Migrated**: Local backup can be used to later migrate your data without any dependance on discord API - even if that server was deleted.