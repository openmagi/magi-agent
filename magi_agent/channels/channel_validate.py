"""Bot-token validation for the dashboard Discord + Slack integrations.

Pure validation against each platform's identity endpoint.  The HTTP call is
injected (``fetch_json(url, token)``) so tests run without network; the token is
used only for the Authorization header and is never logged.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_DISCORD_ME_URL = "https://discord.com/api/v10/users/@me"
_SLACK_AUTH_TEST_URL = "https://slack.com/api/auth.test"

# (url, token) -> parsed JSON.
ChannelFetchJson = Callable[[str, str], dict[str, Any]]


class InvalidBotToken(Exception):
    """Raised when the platform rejects the supplied token."""


def validate_discord_bot_token(token: str, *, fetch_json: ChannelFetchJson) -> dict[str, Any]:
    """Validate a Discord bot token via ``GET /users/@me``.

    Returns ``{"id", "username"}``.  Raises :class:`InvalidBotToken` when the
    token is empty or Discord does not return a bot identity.
    """
    cleaned = (token or "").strip()
    if not cleaned:
        raise InvalidBotToken("empty bot token")

    payload = fetch_json(_DISCORD_ME_URL, cleaned)
    # Discord error payloads carry a "message"/"code" and no "id".
    if not isinstance(payload, dict) or not payload.get("id"):
        raise InvalidBotToken("discord rejected the bot token")

    return {"id": payload.get("id"), "username": payload.get("username")}


def validate_slack_bot_token(token: str, *, fetch_json: ChannelFetchJson) -> dict[str, Any]:
    """Validate a Slack bot token via ``auth.test``.

    Returns ``{"team", "user"}``.  Raises :class:`InvalidBotToken` when the token
    is empty or Slack does not return ``ok: true``.
    """
    cleaned = (token or "").strip()
    if not cleaned:
        raise InvalidBotToken("empty bot token")

    payload = fetch_json(_SLACK_AUTH_TEST_URL, cleaned)
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise InvalidBotToken("slack rejected the bot token")

    return {"team": payload.get("team"), "user": payload.get("user")}


__all__ = [
    "ChannelFetchJson",
    "InvalidBotToken",
    "validate_discord_bot_token",
    "validate_slack_bot_token",
]
