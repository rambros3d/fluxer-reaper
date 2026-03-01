# Server Shuttle

Server Shuttle is a simple tool to help you move an entire Discord server over to a Fluxer community. It handles channels, roles, emojis, and even your message history.

>Join our [**Reaper Community**](https://fluxer.gg/9KxDP8WH) if you need help or have any questions.

![Fluxer Reaper](images/fluxer-reaper.jpg)

| Features | Fluxer | Stoat |
| :--- | :---: | :---: |
| **Server Template** |  |  |
| - Copy Categories & Channels | 🟩 | 🟩 |
| - Category Linking | 🟩 | 🟩 |
| - Channel Topic/Description | 🟩 | 🟩 |
| - NSFW Status | 🟩 | 🟩 |
| - Slowmode | 🟩 | ⚠️ |
| **Roles & Permissions** |  |  |
| - Roles Cloning | 🟩 | 🟩 |
| - Roles Permissions | 🟩 | 🟩 |
| - Category Permissions | 🟩 | ⚠️ |
| - Channel Permissions | 🟩 | ⏳to be done |
| **Emojis & Stickers** |  |  |
| - Copy Emojis | 🟩 | 🟩 |
| - Copy Stickers | 🟩 | ⚠️ |
| **Server Identity** |  |  |
| - Server Name | 🟩 | 🟩 |
| - Server Icon | 🟩 | 🟩 |
| - Server Banner | 🟩 | 🟩 |
| **Message Message history** |  |  |
| - Text Messages | 🟩 | 🟩 |
| - File Attachments | 🟩 | 🟩 *some file extensions not supported*|
| - Preserve Reply Links | 🟩 | 🟩 |
| - Threads | 🟩 | 🟩 |
| - Forums | ⚠️ | ⚠️ |

- ⚠️**Fluxer/Stoat**: Threads & Forums type channels are not yet natively available. As a workaround, threads are migrated in their parent channels.
- ⚠️**Stoat**: doesnt have features like Category Permissions, Slowmode setting for channels.
- ⏳**Stoat**: permission sync for channels is not yet implemented since its permission management vastly different compared to Discord & Fluxer.

---

### Notable Features
*   **Flexible Start Points**: Start from the oldest message, a specific custom message ID/link, or continue from where you left off.
*   **Thread Support**: Copies threads with dedicated markers in the target channel. This is a workaround as the threads feature is not yet natively implemented in Fluxer/Stoat.
*   **Permission Checks**: Checks if bots have the required Permissions.
*   **Self-Hosted Instance Support**: Supports custom API url endpoint to use with your own Fluxer/Stoat instance.
*   **Audit Channel**: Logs migration progress and errors to a logs channel in the target community.

### Danger Zone
*   **Wipe Options**: Irreversibly delete channels, roles, or assets to clear a community.
*   **Permission Reset**: Batch-wipe all channel permission overwrites.

The Discord bot has **read-only access** to the Discord server. Hence **no changes** will be made to the source Discord server.

### Notes:
- Currently Fluxer is unstable probably due to high traffic, migrating servers now will only add to that problem. Only use this for testing purposes for now. Check status on [reallyaweso.me](https://uptime.reallyaweso.me/status/fluxer) or [fluxerstatus(official)](https://fluxerstatus.com)



# Getting Started

#### Setup the bots as per this [guide](BOT-SETUP.md)

### Option 1: Using Pre-built Binaries (Easiest)
1. **Download**: Download the latest executable from the [Releases](https://github.com/rambros3d/server-shuttle/releases) page & unzip it.
2. **Run**: Simply run the downloaded file to start the tool. No Python installation is required.
   - **Linux**: Run the **server-shuttle** file (or `./server-shuttle` in terminal).
   - **Windows**: Run the **server-shuttle.exe** file.

### Option 2: Running from Source
1. **Clone**: Clone the repository to your local machine:
   ```bash
   git clone https://github.com/rambros3d/server-shuttle.git
   cd server-shuttle
   ```
2. **Launch**: Run the appropriate launcher script for your OS. It will automatically create a virtual environment and install dependencies:
   - **Linux**: `./launch-shuttle-LINUX.sh`
   - **MacOS**: `./launch-shuttle-MAC.sh`
   - **Windows**: Double-click `launch-shuttle-WIN.bat`


---
---
## Discord Age Verification Misconceptions

Discord’s latest operation just proves they never cared about you or your kids' safety.

### Age verification protect kids
- Online safety of your kids should be your responsibility.
- The big tech and especially the government should stay away from the kids. Period. 
- Do you think they care about your kids' safety, Really?


### Its the Law
>Discord is rolling out age verification in all countries; even when not required by law.

>**Big tech companies didn't comply with the laws when mishandling our data.**
Now they suddenly seem to care when complying with age verification laws (as they can grab even more data).

- Point being, in either case they never cared about the us. As we are their product anyway
- I dont expect these companies to stand up for our rights against these dystopian laws.


### Discord ID verification "privacy-preserving"

>Discord initially **fooled everyone** that it will be optional and the verification data wont leave the device.
But now their own website states that **Persona** will be used in some countries, its brought to you by the same guy involved with [**Palantir**](https://corbettreport.com/what-does-palantir-actually-do/).

### **Just say NO** to invasive companies & Move to platforms that respects your privacy.

---
---

### Documentation
Detailed info about the code can be found at https://deepwiki.com/rambros3d/server-shuttle

### Vibe Code Notice

- Code is provided as is; This tool was developed with AI.
- Take it, use it, modify it, feel free to do whatever you wish.

---