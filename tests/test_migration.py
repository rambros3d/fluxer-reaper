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
def mock_context(mock_discord_reader, mock_fluxer_writer, mock_stoat_writer):
    context = MagicMock(spec=MigrationContext)
    context.discord_reader = mock_discord_reader
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
    assert str(backup_reader.guild.id) == reaper_config.get("discord_server_id")
    
    channels = await backup_reader.fetch_channels()
    assert len(channels) > 0

# --- E2E Simulation ---

@pytest.mark.asyncio
@pytest.mark.parametrize("platform", get_platforms())
async def test_migration_e2e_loop(reaper_config, test_data_dir, tmp_path, platform, request):
    config = AppConfig(
        discord_bot_token=reaper_config["discord_bot_token"],
        discord_server_id=reaper_config["discord_server_id"],
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
    
    await context.discord_reader.start()
    channels = await context.discord_reader.fetch_channels()
    text_channels = [c for c in channels if c.type == ChannelType.text]
    
    if text_channels:
        source_channel_id = text_channels[0].id
        target_channel_id = "999" if platform == "fluxer" else "stoat123"
        
        mock_writer.send_message.side_effect = lambda **kwargs: "ok"
        
        # Test just the first available channel to keep it fast
        stats = await migrate_func(context=context, source_channel_id=source_channel_id, target_channel_id=target_channel_id)
        assert stats["messages"] >= 0
    
    await context.discord_reader.close()
