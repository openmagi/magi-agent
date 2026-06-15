from __future__ import annotations

import pytest


def test_validate_bot_token_returns_identity_on_ok() -> None:
    from magi_agent.channels.telegram_validate import validate_bot_token

    seen: list[str] = []

    def fetch_json(url: str) -> dict[str, object]:
        seen.append(url)
        return {"ok": True, "result": {"id": 42, "username": "my_bot", "first_name": "Mybot"}}

    identity = validate_bot_token("123:ABC", fetch_json=fetch_json)

    assert identity == {"id": 42, "username": "my_bot", "first_name": "Mybot"}
    assert seen == ["https://api.telegram.org/bot123:ABC/getMe"]


def test_validate_bot_token_rejects_empty() -> None:
    from magi_agent.channels.telegram_validate import InvalidBotToken, validate_bot_token

    with pytest.raises(InvalidBotToken):
        validate_bot_token("   ", fetch_json=lambda _url: {"ok": True})


def test_validate_bot_token_rejects_not_ok() -> None:
    from magi_agent.channels.telegram_validate import InvalidBotToken, validate_bot_token

    def fetch_json(_url: str) -> dict[str, object]:
        return {"ok": False, "error_code": 401, "description": "Unauthorized"}

    with pytest.raises(InvalidBotToken):
        validate_bot_token("bad", fetch_json=fetch_json)


def test_resolve_token_prefers_active_vault_credential() -> None:
    from magi_agent.channels.telegram_credentials import resolve_telegram_bot_token

    creds = {
        "credentials": [
            {"service": "openai", "auth_scheme": "api_key", "status": "active", "vault_ref": "v0"},
            {"service": "telegram", "auth_scheme": "bot_token", "status": "active", "vault_ref": "v9"},
        ]
    }
    secret_calls: list[str] = []

    def get_secret(ref: str) -> str | None:
        secret_calls.append(ref)
        return "vault-token"

    token = resolve_telegram_bot_token(
        env={"MAGI_TELEGRAM_BOT_TOKEN": "env-token"},
        load_credentials=lambda: creds,
        get_secret=get_secret,
    )

    assert token == "vault-token"
    assert secret_calls == ["v9"]


def test_resolve_token_falls_back_to_env() -> None:
    from magi_agent.channels.telegram_credentials import resolve_telegram_bot_token

    token = resolve_telegram_bot_token(
        env={"TELEGRAM_BOT_TOKEN": "env-token"},
        load_credentials=lambda: {"credentials": []},
        get_secret=lambda _ref: None,
    )

    assert token == "env-token"


def test_resolve_token_ignores_non_active_or_wrong_scheme() -> None:
    from magi_agent.channels.telegram_credentials import resolve_telegram_bot_token

    creds = {
        "credentials": [
            {"service": "telegram", "auth_scheme": "bot_token", "status": "revoked", "vault_ref": "v1"},
            {"service": "telegram", "auth_scheme": "api_key", "status": "active", "vault_ref": "v2"},
        ]
    }

    token = resolve_telegram_bot_token(
        env={},
        load_credentials=lambda: creds,
        get_secret=lambda _ref: "should-not-be-used",
    )

    assert token is None


def test_resolve_token_returns_none_when_nothing_configured() -> None:
    from magi_agent.channels.telegram_credentials import resolve_telegram_bot_token

    token = resolve_telegram_bot_token(
        env={},
        load_credentials=lambda: {"credentials": []},
        get_secret=lambda _ref: None,
    )

    assert token is None
