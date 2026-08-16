# tests/live_helpers.py
"""
Shared helpers for live integration tests.

These connect to real Fluxer (or Discord) instances and perform safe,
idempotent setup — channels are found or created, test messages are
only posted to fill gaps, and nothing is deleted so results can be
inspected after a test run.

Message counts are configured via environment variables:

    LIVE_COUNT_A=300   LIVE_COUNT_B=400   LIVE_COUNT_RESUME=200

Default is 300 / 400 / 200 respectively.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

TEST_MSG_PREFIX = "[reaper-test]"


def live_count(env_var: str, default: int) -> int:
    """Read a message count from an environment variable, with a default."""
    try:
        return int(os.environ.get(env_var, str(default)))
    except ValueError:
        return default


def make_fluxer_http(token: str, api_url: str = "default"):
    """Create a fluxer HTTPClient, optionally with a custom API URL."""
    from fluxer import HTTPClient
    kwargs = {}
    if api_url and api_url != "default":
        kwargs["api_url"] = api_url
    return HTTPClient(token, **kwargs)


async def ensure_test_channel(
    http,                 # fluxer.HTTPClient (or discord client)
    guild_id: str,
    base_name: str,       # e.g. "reaper-live"
    target_msg_count: int,
    *,
    is_fluxer: bool = True,
) -> tuple[str, str]:
    """Find (by *base_name* prefix) or create a test channel, seed it with
    *target_msg_count* messages, then rename it to ``{base_name}-{count}``.

    If the channel already exists, existing ``[reaper-test]`` messages are
    counted and only the difference is posted.  Nothing is deleted.

    Returns ``(channel_id, final_channel_name)``.
    """
    channel_id = await _find_or_create_channel(http, guild_id, base_name, is_fluxer)

    if is_fluxer:
        existing = await _count_fluxer_test_messages(http, channel_id)
    else:
        existing = 0

    to_post = max(0, target_msg_count - existing)
    if to_post > 0:
        logger.info("Seeding %d messages in channel %s (already have %d)", to_post, channel_id, existing)
        for i in range(existing + 1, existing + to_post + 1):
            content = f"{TEST_MSG_PREFIX} msg {i} in #{base_name}"
            if is_fluxer:
                await http.send_message(channel_id, content=content)
            else:
                pass  # Discord path not yet implemented
    else:
        logger.info("Channel %s already has %d messages — skipping seed", channel_id, existing)

    final_count = existing + to_post
    final_name = f"{base_name}-{final_count}"

    # Rename channel to reflect the actual count
    if is_fluxer:
        current = await http.get_channel(channel_id)
        if current.get("name") != final_name:
            await http.modify_channel(channel_id, name=final_name)
            logger.info("Renamed channel %s → #%s", channel_id, final_name)

    return channel_id, final_name


async def _find_or_create_channel(http, guild_id: str, base_name: str, is_fluxer: bool) -> str:
    """Return the ID of an existing channel whose name starts with *base_name*,
    or create a new one named *base_name*."""
    if is_fluxer:
        channels = await http.get_guild_channels(guild_id)
        for ch in channels:
            name = ch.get("name", "")
            if name == base_name or name.startswith(f"{base_name}-"):
                logger.info("Found existing channel #%s (%s)", name, ch["id"])
                return ch["id"]
        ch = await http.create_guild_channel(guild_id, name=base_name, type=0)
        logger.info("Created channel #%s (%s)", base_name, ch["id"])
        return ch["id"]
    else:
        raise NotImplementedError("Discord live tests not yet implemented")


async def _count_fluxer_test_messages(http, channel_id: str) -> int:
    """Count how many ``[reaper-test]`` messages exist in *channel_id*.

    Walks backwards through the channel history (up to 2000 messages).
    """
    count = 0
    before_id: Optional[str] = None

    for _ in range(20):  # max 20 pages × 100 = 2000 messages
        params = {"limit": 100}
        if before_id:
            params["before"] = before_id

        batch = await http.get_messages(channel_id, **params)
        if not batch:
            break

        for msg in batch:
            if isinstance(msg.get("content"), str) and msg["content"].startswith(TEST_MSG_PREFIX):
                count += 1

        if len(batch) < 100:
            break
        before_id = batch[-1].get("id")

    return count
