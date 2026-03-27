from typing import Any, Optional
import re
import logging

def parse_snowflake(value: Any) -> Optional[int]:
    """Safely parses a Discord ID (Snowflake) from any input, handling 'None' strings."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "none" or s == "NULL":
        return None
    try:
        return int(s)
    except ValueError:
        return None

from src.core.state import MigrationState

logger = logging.getLogger(__name__)

def resolve_discord_links(content: str, state: MigrationState, platform: str, target_server_id: str) -> str:
    """
    Finds Discord message/channel links and resolves them to the target platform 
    if they have been migrated.
    """
    if not content:
        return content

    # Regex for Discord links: https://discord.com/channels/{guild}/{channel}/{message}
    # Matches: https://discord.com/channels/123/456 or https://discord.com/channels/123/456/789
    discord_link_re = re.compile(r'https?://(?:ptb\.|canary\.)?discord\.com/channels/(\d+)/(\d+)(?:/(\d+))?')

    def replace_link(match):
        full_url = match.group(0)
        
        # Check if already part of a markdown link: [text](link) or [text](<link>)        
        # We look backwards for ]( or ](<
        start_idx = match.start()
        if start_idx > 2:
            prev_chars = content[max(0, start_idx-3):start_idx]
            if prev_chars.endswith("](") or prev_chars.endswith("](<"):
                logger.debug(f"resolve_discord_links: Skipping already-wrapped link: {full_url[:60]}")
                return full_url

        guild_id = match.group(1)
        channel_id = match.group(2)
        message_id = match.group(3)

        target_cid = state.get_target_channel_id(channel_id) or state.get_target_category_id(channel_id)
        logger.debug(f"resolve_discord_links: guild={guild_id} channel={channel_id} msg={message_id} target_cid={target_cid}")
        
        if message_id:
            # Message link resolution
            t_cid, t_mid = state.find_message_mapping(message_id)
            logger.debug(f"resolve_discord_links: find_message_mapping({message_id}) -> t_cid={t_cid}, t_mid={t_mid}")
            if t_mid:
                # Use found channel ID if available, otherwise fallback to channel_id mapping
                final_cid = t_cid or target_cid
                if final_cid:
                    if platform == "stoat":
                        return f"https://stoat.chat/server/{target_server_id}/channel/{final_cid}/{t_mid}"
                    else: # Fluxer
                        return f"https://fluxer.app/channels/{target_server_id}/{final_cid}/{t_mid}"
            
            # Fallback for unmigrated message
            return f"[`discord-message`](<{full_url}>)"
        else:
            # Channel link resolution
            if target_cid:
                if platform == "stoat":
                    return f"https://stoat.chat/server/{target_server_id}/channel/{target_cid}"
                else: # Fluxer
                    return f"https://fluxer.app/channels/{target_server_id}/{target_cid}"
            
            # Fallback for unmapped channel
            return f"[`discord-channel`](<{full_url}>)"


    logger.debug(f"resolve_discord_links: Processing content (len {len(content)}): {content[:100]!r}")
    result = discord_link_re.sub(replace_link, content)
    if result != content:
        logger.debug(f"resolve_discord_links: Content resolved to (len {len(result)}): {result[:100]!r}")
    return result

import subprocess

def get_app_version() -> str:
    """Gets the dynamic app version from baked file or git."""
    try:
        from src.core._baked_version import __version__
        return f"Reaper-{__version__}"
    except ImportError:
        pass
        
    try:
        version = subprocess.check_output(
            ["git", "describe", "--tags", "--always"], 
            stderr=subprocess.DEVNULL,
            universal_newlines=True
        ).strip()
        if not version:
            return "Reaper-Unknown"
        return f"Reaper-{version}"
    except Exception:
        return "Reaper-Unknown-git"
