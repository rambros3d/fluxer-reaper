import pytest
from unittest.mock import MagicMock
from src.core.utils import parse_snowflake, resolve_discord_links

def test_parse_snowflake_valid():
    assert parse_snowflake("12345") == 12345
    assert parse_snowflake(12345) == 12345
    assert parse_snowflake("  67890  ") == 67890

def test_parse_snowflake_invalid():
    assert parse_snowflake(None) is None
    assert parse_snowflake("") is None
    assert parse_snowflake("none") is None
    assert parse_snowflake("NULL") is None
    assert parse_snowflake("not_a_number") is None

def test_resolve_discord_links_no_content():
    assert resolve_discord_links("", None, "fluxer", "target_id") == ""
    assert resolve_discord_links(None, None, "fluxer", "target_id") is None

def test_resolve_discord_links_no_mapping():
    mock_state = MagicMock()
    mock_state.get_target_channel_id.return_value = None
    mock_state.get_target_category_id.return_value = None
    mock_state.find_message_mapping.return_value = (None, None)
    
    content = "Check this: https://discord.com/channels/1/2/3"
    resolved = resolve_discord_links(content, mock_state, "fluxer", "target_server")
    assert "[`discord-message`](<https://discord.com/channels/1/2/3>)" in resolved

def test_resolve_discord_links_channel_mapping():
    mock_state = MagicMock()
    mock_state.get_target_channel_id.return_value = "target_chan_456"
    mock_state.find_message_mapping.return_value = (None, None)
    
    content = "Go to https://discord.com/channels/123/456"
    
    # Test Fluxer
    resolved_fluxer = resolve_discord_links(content, mock_state, "fluxer", "target_server")
    assert "https://fluxer.app/channels/target_server/target_chan_456" in resolved_fluxer
    
    # Test Stoat
    resolved_stoat = resolve_discord_links(content, mock_state, "stoat", "target_server")
    assert "https://stoat.chat/server/target_server/channel/target_chan_456" in resolved_stoat

def test_resolve_discord_links_message_mapping():
    mock_state = MagicMock()
    mock_state.find_message_mapping.return_value = ("target_chan_456", "target_msg_789")
    
    content = "Look at this: https://discord.com/channels/123/456/789"
    
    # Test Fluxer
    resolved_fluxer = resolve_discord_links(content, mock_state, "fluxer", "target_server")
    assert "https://fluxer.app/channels/target_server/target_chan_456/target_msg_789" in resolved_fluxer

def test_resolve_discord_links_skips_wrapped():
    mock_state = MagicMock()
    content = "Already wrapped: [link](https://discord.com/channels/1/2/3)"
    resolved = resolve_discord_links(content, mock_state, "fluxer", "target_server")
    assert resolved == content
