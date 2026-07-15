# DiscoReaper - Help & FAQ

### Ctrl+V doesnt work for pasting the tokens?
- You can try pasting with `Ctrl+Shift+V`, this is also the default keybind for linux systems.
- Windows 10 has been known to be problematic, it seems to use a separate clipboard for the terminal.
- As a workaround, you can directly paste tokens in **config.yaml** file inside the ReaperFiles-XXX folder
```
source_bot_token: YOUR_SOURCE_BOT_TOKEN
fluxer_bot_token: YOUR_FLUXER_BOT_TOKEN
stoat_bot_token: YOUR_STOAT_BOT_TOKEN
```

### Messages are empty, Only Timestamps are migrated?
- The Bot might be missing the **Message Content Intent** Privilege.
- **Enable it** in the Discord Developer Portal under the **Bot** tab.

### Why are some Discord channels are missing?
- If the missing channel has any custom permission overrides, **add the bot role manually** to the **channel or its parent category**, this will allow the bot to access them.
- The bot role may not have access to the private channels in your discord server, Even when you give Read Messages & View Channels permission to the bot role.

---

### Can I use the tool to delete messages in the Discord server?
- **No.** The tool operates using the Discord Bot API with **read-only permissions** (view and read access).
- It does not perform any write actions, so it cannot modify, delete, or change anything in your Discord server.

### Can I migrate Personal messages (DMs)?
- **No.** The tool uses the Discord Bot API, which does not grant access to personal messages (DMs), so they cannot be migrated or exported.


### Which Platforms are supported?
- **Fluxer** and **Stoat** are the supported target platforms.
- Eligibility criteria for new platforms:
    - Open Source
    - Self-Hostable
    - Bot API

---

### Where do I get help?

Ping me in the [Reaper Community](https://fluxer.gg/9KxDP8WH) on Fluxer

Provide the following details when asking for help:
- Your Operating System
- Reaper Version
- Briefly describe your issue