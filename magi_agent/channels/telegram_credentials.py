"""Resolve the Telegram bot token from the local vault, then the environment.

Used by the gateway channel watcher so a token connected via the dashboard
(stored in the vault) starts polling without restart — the watcher re-reads
this each interval. Falls back to the legacy env vars for operator-provisioned
deployments.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

_ENV_KEYS = ("MAGI_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")


def resolve_telegram_bot_token(
    *,
    env: Mapping[str, str] | None = None,
    load_credentials: Callable[[], dict[str, Any]] | None = None,
    get_secret: Callable[[str], str | None] | None = None,
) -> str | None:
    """Return the active bot token (vault first, env fallback) or None."""

    environ = os.environ if env is None else env
    loader = load_credentials or _default_load_credentials
    reader = get_secret or _default_get_secret

    vault_ref = _active_telegram_vault_ref(loader)
    if vault_ref:
        secret = reader(vault_ref)
        if secret and secret.strip():
            return secret.strip()

    for key in _ENV_KEYS:
        value = (environ.get(key) or "").strip()
        if value:
            return value

    return None


def _active_telegram_vault_ref(
    load_credentials: Callable[[], dict[str, Any]],
) -> str | None:
    data = load_credentials() or {}
    for item in data.get("credentials", []):
        if not isinstance(item, dict):
            continue
        if (
            item.get("service") == "telegram"
            and item.get("auth_scheme") == "bot_token"
            and item.get("status") == "active"
        ):
            vault_ref = item.get("vault_ref")
            if isinstance(vault_ref, str) and vault_ref:
                return vault_ref
    return None


def _default_load_credentials() -> dict[str, Any]:
    from magi_agent.credentials_admin import store

    return store.load_credentials()


def _default_get_secret(vault_ref: str) -> str | None:
    from magi_agent.credentials_admin.local_vault import LocalVault

    return LocalVault().get_secret(vault_ref)
