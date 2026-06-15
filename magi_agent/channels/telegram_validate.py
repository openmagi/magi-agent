"""Bot-token validation for the dashboard Telegram integration.

Pure validation against the Telegram Bot API ``getMe`` endpoint. The HTTP call
is injected so tests run without network. The token is used only for URL
construction and is never logged.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_API_BASE = "https://api.telegram.org"


class InvalidBotToken(Exception):
    """Raised when Telegram rejects the supplied bot token."""


def validate_bot_token(
    token: str,
    *,
    fetch_json: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """Validate a bot token via ``getMe`` and return its identity.

    Returns ``{"id", "username", "first_name"}``. Raises :class:`InvalidBotToken`
    when the token is empty or Telegram does not return ``ok: true``.
    """

    cleaned = (token or "").strip()
    if not cleaned:
        raise InvalidBotToken("empty bot token")

    payload = fetch_json(f"{_API_BASE}/bot{cleaned}/getMe")
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise InvalidBotToken("telegram rejected the bot token")

    result = payload.get("result")
    if not isinstance(result, dict):
        raise InvalidBotToken("telegram returned no bot identity")

    return {
        "id": result.get("id"),
        "username": result.get("username"),
        "first_name": result.get("first_name"),
    }
