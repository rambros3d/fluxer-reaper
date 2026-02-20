# Fluxer Reaper 🚀

Fluxer Reaper is a simple tool to help you move your Discord server content over to a Fluxer community. It handles channels, roles, emojis, and even your message history.

![Fluxer Reaper](fluxer-reaper.jpg)

## 🛠️ Getting Started

### 1. Prerequisites
Make sure you have Python installed on your computer.

### 2. Install the tool
Open your terminal and run:
```bash
pip install -r requirements.txt
```

### 3. Setup your config
Open the `config.yaml` file and fill in your details:
*   **Discord Bot Token**: Your bot's token from the Discord Developer Portal.
*   **Discord Server ID**: The ID of the server you want to copy.
*   **Fluxer Bot Token**: Your Fluxer bot token.
*   **Fluxer Community ID**: The ID of the Fluxer community where you want to move everything.

### 4. Run it!
Start the tool by running:
```bash
python fluxer-reaper.py
```

## ✨ Features
*   **Clone Server Structure**: Automatically creates all your Discord categories and channels in Fluxer.
*   **Copy Roles**: Copies your roles and their basic permissions.
*   **Copy Emojis & Stickers**: Copies your custom emojis and stickers over.
*   **Message History**: Select a specific channel and migrate messages starting from the oldest or a custom point.
*   **Server Identity**: Syncs your server name, icon, and banner.

## ⚠️ Important Tips
*   **Token Validation**: The tool will double-check your tokens every time you start it or change settings.
*   **Safety**: You will always be asked for confirmation before any major action (like deleting or creating many items).
