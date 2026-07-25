# DiscoReaper Operations

## 0. Source Platforms

DiscoReaper supports two source platforms:

| Source | Modes available |
|---|---|
| **Discord** | Direct migration, Backup, Backup-then-migrate |
| **Fluxer** | Direct migration only (backup coming later) |

To use a Fluxer source, set **Source Platform** to `fluxer` when creating a config. You'll need:
- A Fluxer bot token (from the Developer Portal or your self-hosted instance)
    - The Fluxer Bot should be given Administrator for ease of use
- The community (guild) ID you want to migrate from
- If self-hosted: the API URL (e.g. `https://your-instance.com/api`)

---

## 1. Migration & Shuttle Tools
The primary suite of tools for moving data between Discord & Fluxer/Stoat.

*   **Shuttle Migration (Direct)**: 
    Connects directly to your source platform and target platform to migrate categories, channels, roles, and messages in real-time. Supports Discord and Fluxer as sources. Uses intelligent mapping to ensure links and mentions work in the new community.
*   **Resumed Migration**: 
    If a large migration is interrupted or hits a rate limit, the Resumed Migration tool uses the local SQLite database to pick up exactly where it left off, skipping messages that have already been transferred.
*   **Offline Migration (from Backup)**: 
    Allows you to migrate data without reaching out to Discord's servers. It uses a previously created local backup as the source, making it the safest and fastest way to migrate if the original Discord server has already been deleted or restricted.
*   **Batch Channel Processing**:
    Allows you to select up to 50 specific channels or categories to migrate at once. Reaper will process them sequentially to stay within platform rate limits.

---

## 2. Backup & Archiving
Tools for creating and maintaining permanent local copies of your data.

*   **Full Server Backup**: 
    Downloads the entire server structure-including all categories, channels, custom roles, and permission overwrites-along with full message histories and all file attachments into a high-performance SQLite database.
*   **Incremental Syncing**: 
    Scans your existing local backup database and only downloads messages sent since the last run. This is extremely efficient for keeping a continuous archive of active communities.
*   **Media Deduplication**: 
    An background operation that ensures identical file attachments (images, stickers, etc.) are only stored once on your hard drive, even if they appear in multiple messages or channels.

---

## 3. Danger Zone (Advanced Management)
Powerful tools for server cleanup and reset. Use with caution.

*   **Server Wipe**: 
    A high-speed utility that deletes all categories, channels, and custom roles on a target server. This is used to "reset" a community before testing a fresh migration. Requires explicit confirmation.
*   **Permission Purge**: 
    Scans every channel and category on the target server and removes all role and user-specific permission overwrites, restoring the server to its default permission state.

---