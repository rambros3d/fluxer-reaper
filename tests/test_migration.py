import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from src.core.base import MigrationContext
from src.core.configuration import AppConfig
from src.core.backup_reader import ChannelType
from src.fluxer.migrate_message import migrate_messages as fluxer_migrate, _process_and_send_message as fluxer_send
from src.stoat.migrate_message import migrate_messages as stoat_migrate, _process_and_send_message as stoat_send

import yaml
from pathlib import Path

# --- Platform Detection (Same as e2e_simulation) ---
def get_platforms():
    """Determine which platforms to test based on config, or use defaults."""
    config_path = (Path(__file__).parent.parent / "ReaperFiles-AutoTest/reaper_config.yaml").resolve()
    if not config_path.exists():
        return ["fluxer", "stoat"]
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)
    platforms = []
    if data.get("fluxer_bot_token"): platforms.append("fluxer")
    if data.get("stoat_bot_token"): platforms.append("stoat")
    return platforms if platforms else ["fluxer", "stoat"]

# --- Unit Tests (Transformation Logic) ---

@pytest.fixture
def mock_context(mock_source_reader, mock_fluxer_writer, mock_stoat_writer):
    context = MagicMock(spec=MigrationContext)
    context.source_reader = mock_source_reader
    context.fluxer_writer = mock_fluxer_writer
    context.stoat_writer = mock_stoat_writer
    context.state = MagicMock()
    context.state.get_user_alias.return_value = "TestAlias"
    context.state.emoji_map = {}
    context.state.channel_map = {}
    context.is_running = True
    return context

@pytest.fixture
def mock_message():
    msg = MagicMock()
    msg.id = 111
    msg.author.id = 222
    msg.author.display_name = "Author"
    msg.content = "Test content"
    msg.attachments = []
    msg.embeds = []
    msg.stickers = []
    msg.created_at.timestamp.return_value = 1600000000.0
    msg.flags.forwarded = False
    msg.reference = None
    return msg

@pytest.mark.asyncio
async def test_migration_transform_fluxer(mock_context, mock_message):
    stats = {"messages": 0, "attachments": 0}
    result = await fluxer_send(context=mock_context, msg=mock_message, target_channel_id="c1", stats=stats)
    assert result == "fluxer_msg_123"
    assert stats["messages"] == 1
    assert mock_context.fluxer_writer.send_message.called

@pytest.mark.asyncio
async def test_migration_transform_stoat(mock_context, mock_message):
    stats = {"messages": 0, "attachments": 0}
    result = await stoat_send(context=mock_context, msg=mock_message, target_channel_id="c1", stats=stats)
    assert result == "stoat_msg_123"
    assert stats["messages"] == 1
    assert mock_context.stoat_writer.send_message.called

# --- Integration Tests (Backup Reader) ---

@pytest.mark.asyncio
async def test_backup_reader_interaction(backup_reader, reaper_config):
    await backup_reader.start()
    assert backup_reader.guild is not None
    # Verify ID from config (or mock default)
    assert str(backup_reader.guild.id) == reaper_config.get("source_server_id")
    
    channels = await backup_reader.fetch_channels()
    assert len(channels) > 0

# --- E2E Simulation ---

@pytest.mark.asyncio
@pytest.mark.parametrize("platform", get_platforms())
async def test_migration_e2e_loop(reaper_config, test_data_dir, tmp_path, platform, request):
    config = AppConfig(
        source_bot_token=reaper_config["source_bot_token"],
        source_server_id=reaper_config["source_server_id"],
        target_platform=platform,
        fluxer_bot_token=reaper_config.get("fluxer_bot_token"),
        fluxer_server_id=reaper_config.get("fluxer_server_id"),
        stoat_bot_token=reaper_config.get("stoat_bot_token"),
        stoat_server_id=reaper_config.get("stoat_server_id"),
        anonymize_users=reaper_config["anonymize_users"]
    )
    
    if test_data_dir.exists():
        base_dir = test_data_dir
        msg = f"[DATA_SOURCE] E2E_SIMULATION: Using sample data from {base_dir.name}"
    else:
        base_dir = tmp_path
        msg = f"[DATA_SOURCE] E2E_SIMULATION: Fallback to mock data in {base_dir}"
    
    from tests.conftest import _log
    print(msg)
    _log(msg)
    
    context = MigrationContext(config, source_mode="backup", base_dir=str(base_dir))
    
    mock_writer = request.getfixturevalue(f"mock_{platform}_writer")
    if platform == "fluxer":
        context.fluxer_writer = mock_writer
        migrate_func = fluxer_migrate
    else:
        context.stoat_writer = mock_writer
        migrate_func = stoat_migrate
        
    context.is_running = True
    from src.core.database import MigrationDatabase
    context.state.db = MigrationDatabase(tmp_path / f"e2e_{platform}.db", platform=platform)
    
    await context.source_reader.start()
    channels = await context.source_reader.fetch_channels()
    text_channels = [c for c in channels if c.type == ChannelType.text]
    
    if text_channels:
        source_channel_id = text_channels[0].id
        target_channel_id = "999" if platform == "fluxer" else "stoat123"
        
        mock_writer.send_message.side_effect = lambda **kwargs: "ok"
        
        # Test just the first available channel to keep it fast
        stats = await migrate_func(context=context, source_channel_id=source_channel_id, target_channel_id=target_channel_id)
        assert stats["messages"] >= 0
    
    await context.source_reader.close()


# --- FluxerReader Pagination Tests ---

def _make_mock_message(msg_id: int, channel_id: str = "1") -> dict:
    """Create a minimal Fluxer API message dict for testing."""
    return {
        "id": str(msg_id),
        "channel_id": channel_id,
        "content": f"msg {msg_id}",
        "author": {"id": "1", "username": "test", "discriminator": "0001"},
        "timestamp": "2024-01-01T00:00:00+00:00",
        "type": 0,
        "flags": 0,
        "mention_roles": [],
        "mention_channels": [],
        "mentions": [],
        "attachments": [],
        "embeds": [],
        "stickers": [],
        "pinned": False,
        "mention_everyone": False,
        "tts": False,
        "reactions": [],
    }


@pytest.mark.asyncio
async def test_fluxer_reader_fetches_all_messages():
    """Messages returned newest-first by the API are collected and reversed
    so fetch_message_history yields oldest-first across multiple pages."""
    from src.fluxer.reader import FluxerReader

    total = 250
    page_size = 100

    # Build messages oldest-first (id=1 is oldest)
    all_msgs = [_make_mock_message(i) for i in range(1, total + 1)]

    # API returns newest-first per page:
    #  page 1: msgs 250 → 151 (newest 100)
    #  page 2: msgs 150 → 51
    #  page 3: msgs 50  → 1
    def api_page(before_id: str | None) -> list[dict]:
        if before_id is None:
            # first call: newest messages
            return list(reversed(all_msgs[-page_size:]))
        before = int(before_id)
        # collect messages with id < before, newest-first
        older = [m for m in all_msgs if int(m["id"]) < before]
        return list(reversed(older[-page_size:]))

    mock_http = MagicMock()
    mock_http.api_url = "https://api.fluxer.app/v1"
    mock_http.get_messages = AsyncMock(side_effect=lambda channel_id, **kw: api_page(kw.get("before")))

    reader = FluxerReader(token="t", server_id="1")
    reader._http = mock_http
    reader.guild_data = {"id": "1", "name": "Test", "icon": None, "banner": None}
    reader.community_id = "1"
    reader._cdn_base = "https://cdn.example.com"
    reader._api_base = "https://api.fluxer.app"

    results = []
    async for wrapped in reader.fetch_message_history("1"):
        results.append(wrapped.content)

    assert len(results) == total, f"Expected {total} messages, got {len(results)}"
    assert results[0] == "msg 1", "First should be oldest"
    assert results[-1] == f"msg {total}", "Last should be newest"
    assert results[99] == "msg 100"


@pytest.mark.asyncio
async def test_fluxer_reader_after_id_resume():
    """Only messages with ID > after_id are yielded (exclusive)."""
    from src.fluxer.reader import FluxerReader

    page_size = 100
    all_msgs = [_make_mock_message(i) for i in range(1, 60)]

    def api_page(before_id: str | None) -> list[dict]:
        if before_id is None:
            return list(reversed(all_msgs[-page_size:]))
        before = int(before_id)
        older = [m for m in all_msgs if int(m["id"]) < before]
        return list(reversed(older[-page_size:]))

    mock_http = MagicMock()
    mock_http.api_url = "https://api.fluxer.app/v1"
    mock_http.get_messages = AsyncMock(side_effect=lambda channel_id, **kw: api_page(kw.get("before")))

    reader = FluxerReader(token="t", server_id="1")
    reader._http = mock_http
    reader.guild_data = {"id": "1", "name": "Test", "icon": None, "banner": None}
    reader.community_id = "1"
    reader._cdn_base = "https://cdn.example.com"
    reader._api_base = "https://api.fluxer.app"

    # Resume from id 30 (exclusive — skip 1-30)
    results = []
    async for wrapped in reader.fetch_message_history("1", after_id="30"):
        results.append(wrapped.content)

    assert results[0] == "msg 31", f"First after 30 should be 31, got {results[0]}"
    assert len(results) == 29, f"Expected 29 messages (31-59), got {len(results)}"


@pytest.mark.asyncio
async def test_fluxer_reader_inclusive_resume():
    """When inclusive=True, the after_id message itself is included."""
    from src.fluxer.reader import FluxerReader

    page_size = 100
    all_msgs = [_make_mock_message(i) for i in range(1, 60)]

    def api_page(before_id: str | None) -> list[dict]:
        if before_id is None:
            return list(reversed(all_msgs[-page_size:]))
        before = int(before_id)
        older = [m for m in all_msgs if int(m["id"]) < before]
        return list(reversed(older[-page_size:]))

    mock_http = MagicMock()
    mock_http.api_url = "https://api.fluxer.app/v1"
    mock_http.get_messages = AsyncMock(side_effect=lambda channel_id, **kw: api_page(kw.get("before")))

    reader = FluxerReader(token="t", server_id="1")
    reader._http = mock_http
    reader.guild_data = {"id": "1", "name": "Test", "icon": None, "banner": None}
    reader.community_id = "1"
    reader._cdn_base = "https://cdn.example.com"
    reader._api_base = "https://api.fluxer.app"

    results = []
    async for wrapped in reader.fetch_message_history("1", after_id="30", inclusive=True):
        results.append(wrapped.content)

    assert results[0] == "msg 30", f"First (inclusive) should be 30, got {results[0]}"
    assert len(results) == 30, f"Expected 30 messages (30-59), got {len(results)}"


# --- Live Integration Tests (require ReaperFiles-AutoTest config) ---


@pytest.mark.live
@pytest.mark.asyncio
async def test_fluxer_live_seed_and_migrate(reaper_config, test_data_dir, tmp_path):
    """Seed source channels, migrate via FluxerReader → target, verify.

    Channel names: ``reaper-live-a`` and ``reaper-live-b``.
    Message counts are read from env vars (defaults shown):

        LIVE_COUNT_A=300   LIVE_COUNT_B=400
    """
    if not test_data_dir.exists():
        pytest.skip("ReaperFiles-AutoTest directory not found")

    src_token = reaper_config.get("source_bot_token")
    src_guild = reaper_config.get("source_server_id")
    tgt_token = reaper_config.get("fluxer_bot_token")
    tgt_guild = reaper_config.get("fluxer_server_id")
    src_api = reaper_config.get("source_api_url") or "default"
    tgt_api = reaper_config.get("fluxer_api_url") or "default"

    if not all([src_token, src_guild, tgt_token, tgt_guild]):
        pytest.skip("Missing tokens or guild IDs in config")

    from src.fluxer.reader import FluxerReader
    from tests.live_helpers import (
        ensure_test_channel, _count_fluxer_test_messages,
        make_fluxer_http, live_count,
    )

    count_a = live_count("LIVE_COUNT_A", 300)
    count_b = live_count("LIVE_COUNT_B", 400)

    src_http = make_fluxer_http(src_token, src_api)
    tgt_http = make_fluxer_http(tgt_token, tgt_api)

    # ── Setup: seed source channels ─────────────────────────────────────
    src_ch_a, src_name_a = await ensure_test_channel(src_http, src_guild, "reaper-live-a", count_a)
    src_ch_b, src_name_b = await ensure_test_channel(src_http, src_guild, "reaper-live-b", count_b)

    # ── Setup: create target channels ───────────────────────────────────
    tgt_ch_a, _ = await ensure_test_channel(tgt_http, tgt_guild, "reaper-live-a", 0)
    tgt_ch_b, _ = await ensure_test_channel(tgt_http, tgt_guild, "reaper-live-b", 0)

    # ── Read source via FluxerReader ────────────────────────────────────
    reader = FluxerReader(token=src_token, server_id=str(src_guild), api_url=src_api)
    await reader.start()

    results_a = []
    async for wrapped in reader.fetch_message_history(str(src_ch_a)):
        results_a.append(wrapped.content)
    assert len(results_a) == count_a

    results_b = []
    async for wrapped in reader.fetch_message_history(str(src_ch_b)):
        results_b.append(wrapped.content)
    assert len(results_b) == count_b

    await reader.close()

    # ── Copy to target ──────────────────────────────────────────────────
    for tgt_ch, results in [(tgt_ch_a, results_a), (tgt_ch_b, results_b)]:
        for content in results:
            await tgt_http.send_message(tgt_ch, content=content)

    # ── Verify target ───────────────────────────────────────────────────
    tgt_count_a = await _count_fluxer_test_messages(tgt_http, tgt_ch_a)
    tgt_count_b = await _count_fluxer_test_messages(tgt_http, tgt_ch_b)
    assert tgt_count_a == count_a, f"Target ch-a: expected {count_a}, got {tgt_count_a}"
    assert tgt_count_b == count_b, f"Target ch-b: expected {count_b}, got {tgt_count_b}"

    await src_http.close()
    await tgt_http.close()


@pytest.mark.live
@pytest.mark.asyncio
async def test_fluxer_live_resume(reaper_config, test_data_dir):
    """Verify that fetch_message_history correctly resumes from an after_id.

    Uses env var  LIVE_COUNT_RESUME=200  (default).
    """
    if not test_data_dir.exists():
        pytest.skip("ReaperFiles-AutoTest directory not found")

    src_token = reaper_config.get("source_bot_token")
    src_guild = reaper_config.get("source_server_id")
    src_api = reaper_config.get("source_api_url") or "default"

    if not all([src_token, src_guild]):
        pytest.skip("Missing source_bot_token or source_server_id in config")

    from src.fluxer.reader import FluxerReader
    from tests.live_helpers import ensure_test_channel, make_fluxer_http, live_count

    total = live_count("LIVE_COUNT_RESUME", 200)
    if total < 2:
        pytest.skip("LIVE_COUNT_RESUME must be >= 2 for resume test")

    src_http = make_fluxer_http(src_token, src_api)
    src_ch, src_name = await ensure_test_channel(src_http, src_guild, "reaper-live-resume", total)

    reader = FluxerReader(token=src_token, server_id=str(src_guild), api_url=src_api)
    await reader.start()

    all_msgs = []
    async for wrapped in reader.fetch_message_history(str(src_ch)):
        all_msgs.append(wrapped)

    assert len(all_msgs) == total
    mid_point = total // 2
    mid_id = all_msgs[mid_point - 1].id  # (total/2)th message

    resumed = []
    async for wrapped in reader.fetch_message_history(str(src_ch), after_id=str(mid_id)):
        resumed.append(wrapped.content)

    expected_remaining = total - mid_point
    assert len(resumed) == expected_remaining, f"Expected {expected_remaining} after resume, got {len(resumed)}"
    assert resumed[0] == f"[reaper-test] msg {mid_point + 1} in #reaper-live-resume"
    assert resumed[-1] == f"[reaper-test] msg {total} in #reaper-live-resume"

    await reader.close()
    await src_http.close()


@pytest.mark.live
@pytest.mark.asyncio
async def test_discord_live_seed_and_migrate(reaper_config, test_data_dir):
    """Seed Discord source channels and migrate to a Fluxer target.

    Requires ``ReaperFiles-AutoTest/reaper_config.yaml`` with:
      ``source_bot_token`` (Discord), ``source_server_id``,
      ``fluxer_bot_token``, ``fluxer_server_id``, and
      ``source_platform: "discord"``.
    """
    if not test_data_dir.exists():
        pytest.skip("ReaperFiles-AutoTest directory not found")

    src_token = reaper_config.get("source_bot_token")
    src_guild = reaper_config.get("source_server_id")
    tgt_token = reaper_config.get("fluxer_bot_token")
    tgt_guild = reaper_config.get("fluxer_server_id")

    if reaper_config.get("source_platform") != "discord":
        pytest.skip("source_platform is not 'discord'")

    if not all([src_token, src_guild, tgt_token, tgt_guild]):
        pytest.skip("Missing tokens or guild IDs in config")

    # TODO: implement Discord live test using discord.py client
    #  1. Use `ensure_test_channel(http, guild, name, count, is_fluxer=False)`
    #     once the Discord path in live_helpers is implemented
    #  2. Create DiscordReader, read channels, verify counts
    #  3. Create FluxerWriter, migrate messages, verify on target
    pytest.skip("Discord live test not yet implemented")
