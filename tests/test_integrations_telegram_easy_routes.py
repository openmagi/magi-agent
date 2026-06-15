from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from magi_agent.channels.telegram_easy import EasySessionStore
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.credentials_admin.store import credentials_path
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.transport.integrations import register_integrations_routes

TOKEN = "local-token"
HEADERS = {"x-gateway-token": TOKEN}


def _runtime() -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token=TOKEN,
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )


class _FakeBotFather:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def converse(self, text: str) -> str:
        self.sent.append(text)
        if text == "/newbot":
            return "How are we going to call it?"
        if len(self.sent) == 2:
            return "Now choose a username."
        return "Done! 999:ZZToken_zzzzzzzzzzzzzzzzzzzzzzzzzzzz"


class _FakeAuthPort:
    def __init__(self, *, two_factor: bool = False) -> None:
        self._two_factor = two_factor
        self.logged_out: list[str] = []

    def send_code(self, phone: str) -> tuple[str, str]:
        return ("sess", "hash")

    def sign_in(self, *, session: str, phone: str, code: str, phone_code_hash: str) -> str:
        from magi_agent.channels.telegram_easy import TwoFactorRequired

        if self._two_factor:
            raise TwoFactorRequired()
        return "auth"

    def check_password(self, *, session: str, password: str) -> str:
        return "auth"

    def botfather(self, session: str) -> _FakeBotFather:
        return _FakeBotFather()

    def log_out(self, session: str) -> None:
        self.logged_out.append(session)


def _client(
    monkeypatch,
    tmp_path,
    *,
    easy_enabled: bool = True,
    port=None,
    two_factor: bool = False,
) -> TestClient:
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAGI_LOCAL_VAULT_ENABLED", "1")
    monkeypatch.setenv("MAGI_VAULT_DIR", str(tmp_path / "vault"))
    if easy_enabled:
        monkeypatch.setenv("MAGI_TELEGRAM_EASY_SETUP_ENABLED", "1")
        monkeypatch.setenv("TELEGRAM_API_ID", "12345")
        monkeypatch.setenv("TELEGRAM_API_HASH", "abc")
    else:
        monkeypatch.delenv("MAGI_TELEGRAM_EASY_SETUP_ENABLED", raising=False)
    target = credentials_path()
    if target.exists():
        target.unlink()
    app = FastAPI()
    register_integrations_routes(
        app,
        _runtime(),
        telegram_fetch_json=lambda url: {
            "ok": True,
            "result": {"id": 9, "username": "my_agent_bot", "first_name": "A"},
        },
        telegram_auth_port_provider=lambda: port or _FakeAuthPort(two_factor=two_factor),
        easy_session_store=EasySessionStore(),
        now_fn=lambda: 1000.0,
    )
    return TestClient(app)


def test_easy_disabled_returns_409(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path, easy_enabled=False)
    resp = client.post(
        "/v1/admin/integrations/telegram/easy/send-code",
        headers=HEADERS,
        json={"phone": "+1"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"] == "telegram_easy_disabled"


def test_aggregate_exposes_easy_available(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    body = client.get("/v1/admin/integrations", headers=HEADERS).json()
    assert body["telegram"]["easy_available"] is True


def test_easy_happy_path_no_2fa(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    sc = client.post(
        "/v1/admin/integrations/telegram/easy/send-code",
        headers=HEADERS,
        json={"phone": "+15551234567"},
    ).json()
    session_id = sc["session_id"]

    vc = client.post(
        "/v1/admin/integrations/telegram/easy/verify-code",
        headers=HEADERS,
        json={"session_id": session_id, "code": "00000"},
    ).json()
    assert vc["needs_2fa"] is False

    cb = client.post(
        "/v1/admin/integrations/telegram/easy/create-bot",
        headers=HEADERS,
        json={"session_id": session_id, "bot_name": "My Agent"},
    )
    assert cb.status_code == 200
    assert cb.json()["telegram"]["configured"] is True


def test_easy_2fa_branch(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path, two_factor=True)
    session_id = client.post(
        "/v1/admin/integrations/telegram/easy/send-code",
        headers=HEADERS,
        json={"phone": "+1"},
    ).json()["session_id"]

    vc = client.post(
        "/v1/admin/integrations/telegram/easy/verify-code",
        headers=HEADERS,
        json={"session_id": session_id, "code": "00000"},
    ).json()
    assert vc["needs_2fa"] is True

    p2 = client.post(
        "/v1/admin/integrations/telegram/easy/verify-2fa",
        headers=HEADERS,
        json={"session_id": session_id, "password": "hunter2"},
    )
    assert p2.status_code == 200
    assert p2.json()["ok"] is True


def test_easy_unknown_session_404(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    resp = client.post(
        "/v1/admin/integrations/telegram/easy/verify-code",
        headers=HEADERS,
        json={"session_id": "nope", "code": "1"},
    )
    assert resp.status_code == 404


def test_default_auth_port_none_without_telethon(monkeypatch, tmp_path) -> None:
    # Easy gate ON but no injected port and the telegram-easy extra is not
    # installed → the default provider resolves to None → 409 (not a 500).
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAGI_TELEGRAM_EASY_SETUP_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc")
    app = FastAPI()
    register_integrations_routes(app, _runtime())  # no port injected
    client = TestClient(app)
    resp = client.post(
        "/v1/admin/integrations/telegram/easy/send-code",
        headers=HEADERS,
        json={"phone": "+1"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"] == "telegram_easy_disabled"
