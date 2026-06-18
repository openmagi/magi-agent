"""Resolve a channel credential from the local vault, then the environment.

Generic version of ``telegram_credentials.resolve_telegram_bot_token`` used by
the Discord and Slack gateway watchers so a token connected via the dashboard
(stored in the vault) starts the channel without a restart — the watcher re-reads
this each interval.  Falls back to env vars for operator-provisioned deployments.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any


def resolve_channel_credential(
    *,
    service: str,
    auth_scheme: str,
    env_keys: tuple[str, ...],
    env: Mapping[str, str] | None = None,
    load_credentials: Callable[[], dict[str, Any]] | None = None,
    get_secret: Callable[[str], str | None] | None = None,
) -> str | None:
    """Return the active credential (vault first, env fallback) or None."""
    environ = os.environ if env is None else env
    loader = load_credentials or _default_load_credentials
    reader = get_secret or _default_get_secret

    vault_ref = _active_vault_ref(loader, service=service, auth_scheme=auth_scheme)
    if vault_ref:
        secret = reader(vault_ref)
        if secret and secret.strip():
            return secret.strip()

    for key in env_keys:
        value = (environ.get(key) or "").strip()
        if value:
            return value

    return None


def _active_vault_ref(
    load_credentials: Callable[[], dict[str, Any]],
    *,
    service: str,
    auth_scheme: str,
) -> str | None:
    data = load_credentials() or {}
    for item in data.get("credentials", []):
        if not isinstance(item, dict):
            continue
        if (
            item.get("service") == service
            and item.get("auth_scheme") == auth_scheme
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


__all__ = ["resolve_channel_credential"]
