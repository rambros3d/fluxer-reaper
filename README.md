# Discord Reaper (to Fluxer)

A state-of-the-art highly resumable interactive CLI for cloning a Discord server to a Fluxer community.

## Project Architecture

The architecture separates the generic orchestration UI from the target and source logic.
- `src/discord_bot/reader.py`: Wraps `discord.py` to fetch server items, channels, users, roles, and message histories.
- `src/fluxer_bot/writer.py`: Wraps `fluxer.py` and creates mirrored items in Fluxer.
- `src/core/engine.py`: Manages reading from one end, writing to the other, and keeping state in `state.json`.
- `src/ui/app.py`: The `rich`-based interactive CLI that binds it all together in an elegant terminal app.

## Setup Instructions

1. Install requirements:
   ```bash
   pip install -r requirements.txt
   ```
2. Configure your properties in `config.yaml` with the correct Discord Bot Token, Fluxer Bot Token, and IDs.
3. Run the application:
   ```bash
   python main.py
   ```

## Production Suggestions

### 1. Speed & Concurrency
- **Asynchronous Batching**: The current engine reads one message and writes one message. To boost speed significantly without being rate-limited, read history aggressively using `asyncio.gather` for attachments and embeds, but queue write requests. 
- **Database Backend**: Migrate `state.json` to `aiosqlite`. A JSON file becomes a bottleneck when state has hundreds of thousands of keys and gets rewritten constantly to disk. SQLite ensures safe concurrent commits.

### 2. Safety & Error Recovery
- **Rate Limit Handlers**: `discord.py` natively respects 429s for Discord. For Fluxer, implement a leaky bucket or backoff wrapper inside `fluxer_bot/writer.py` to avoid 429 HTTP bans. Make sure the TUI reports when a rate-limit sleep occurs.
- **Attachment Caching**: When copying heavy attachments, stream to a temporary local disk file (e.g. `/tmp/discord_reaper/`), upload it to Fluxer, then dynamically delete it. Holding attachments in RAM will cause OOM crashes on large media servers.
- **Resumability Constraints**: Expand `state.py` to store not just channel offsets, but also thread IDs and role mapping hashes, ensuring total state recovery on unexpected exits.

### 3. CLI UX Improvements
- **Interactive Selectors**: Replace the static "Run Migration" with a dynamic prompt menu using `rich` or `questionary` that lists Discord categories and channels. Users should be able to check/uncheck categories they want to exclude from the migration.
- **Color Coding**: Map log levels (INFO, ERROR, WARN) to Rich console colors (green, red, yellow).
- **Graceful Abort**: The tool gracefully handles exit commands, but adding a hotkey listener or `asyncio.Task.cancel()` for immediate stops during hanging requests could enhance responsiveness.
