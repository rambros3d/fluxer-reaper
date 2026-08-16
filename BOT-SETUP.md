
# Bot Setup

> Discord can only be used as a **source** platform. Fluxer can be a source and/or target.
> If you're using a **self-hosted Fluxer instance**, create the bot application in your own instance's settings (by going to the app: User Settings > Applications ) instead of the official Fluxer app — the steps below are the same.

# 1) Fluxer Bot

Go to your **user settings** in Fluxer (**Ctrl** + **,**) and **Create Application**
![Fluxer Step 1](images/fluxer-bot-1.png)

**Name your bot**
![Fluxer Step 2](images/fluxer-bot-2.png)

**Regenerate** & copy the **Bot Token**. Save it somewhere safe, you will need it later for the tool
![Fluxer Step 3](images/fluxer-bot-3.png)

Scroll down to **Scopes** and select **bot**. Choose the permissions your bot needs:

| Role | Permissions needed |
|---|---|
| **Source** (reading from a community) | View Channels, Read Messages OR (if not working) **Administrator**
| **Target** (writing to a community) | Administrator

If you're using the same community as both source and target, or if you're unsure, just give **Administrator** — it covers everything.
![Fluxer Step 4](images/fluxer-bot-4.png)

Choose your **Fluxer Community** (Preferably create and use a new one)
![Fluxer Step 5](images/fluxer-bot-5.png)

**Authorize** & Add the bot to your community
![Fluxer Step 6](images/fluxer-bot-6.png)


## 2) Discord Bot

Go to https://discord.com/developers/applications & create **New Application**
![Discord Step 1](images/discord-bot-1.png)

**Name your bot**
![Discord Step 2](images/discord-bot-2.png)

In the bot Settings, enable **Message Content Intent**
![Discord Step 3](images/discord-bot-3.png)

**Regenerate** & copy the **Bot Token**. Save it somewhere safe, you will need it later for the tool
![Discord Step 4](images/discord-bot-4.png)

Under **Installation** section, enable these:
- Scopes: **bot**
- Permissions: **Read Message History** , **View Channels**

![Discord Step 5](images/discord-bot-5.png)

Finally open the **Install link** and add the bot to your Discord server
![Discord Step 6](images/discord-bot-6.png)

---