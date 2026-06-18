"""Vault-first credential resolution for discord/slack channel watchers (PR4).

The dashboard stores channel tokens in the vault; the watcher must read them
(vault first, env fallback) so a token connected via the dashboard starts the
channel without a restart — mirroring telegram_credentials.resolve.
"""
from __future__ import annotations

from typing import Any

from magi_agent.channels.channel_credentials import resolve_channel_credential


def _creds(*items: dict[str, Any]) -> dict[str, Any]:
    return {"credentials": list(items)}


def test_resolves_from_vault_when_present() -> None:
    load = lambda: _creds(
        {
            "service": "discord",
            "auth_scheme": "bot_token",
            "status": "active",
            "vault_ref": "ref-1",
        }
    )
    token = resolve_channel_credential(
        service="discord",
        auth_scheme="bot_token",
        env_keys=("MAGI_DISCORD_BOT_TOKEN",),
        env={},
        load_credentials=load,
        get_secret=lambda ref: "vault-tok" if ref == "ref-1" else None,
    )
    assert token == "vault-tok"


def test_falls_back_to_env_when_no_vault_entry() -> None:
    token = resolve_channel_credential(
        service="discord",
        auth_scheme="bot_token",
        env_keys=("MAGI_DISCORD_BOT_TOKEN", "DISCORD_BOT_TOKEN"),
        env={"DISCORD_BOT_TOKEN": "env-tok"},
        load_credentials=lambda: _creds(),
        get_secret=lambda ref: None,
    )
    assert token == "env-tok"


def test_returns_none_when_nothing_configured() -> None:
    token = resolve_channel_credential(
        service="slack",
        auth_scheme="app_token",
        env_keys=("MAGI_SLACK_APP_TOKEN",),
        env={},
        load_credentials=lambda: _creds(),
        get_secret=lambda ref: None,
    )
    assert token is None


def test_ignores_revoked_vault_entries() -> None:
    load = lambda: _creds(
        {
            "service": "slack",
            "auth_scheme": "bot_token",
            "status": "revoked",
            "vault_ref": "ref-x",
        }
    )
    token = resolve_channel_credential(
        service="slack",
        auth_scheme="bot_token",
        env_keys=("MAGI_SLACK_BOT_TOKEN",),
        env={},
        load_credentials=load,
        get_secret=lambda ref: "should-not-be-used",
    )
    assert token is None
