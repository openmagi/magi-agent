"""U4 transport: egress allowlist GET/PUT + config-change audit rows (design 5.5/5.6).

The persisted allowlist lives under ``customize.json`` key
``egress_guard.allowlist``. A GET/PUT pair exposes it. Every weakening change
that goes through the transport endpoints -- an allowlist write, a mode change,
and a builtin-policy toggle -- emits a config-change audit row through the
process-global observability sink so tampering (and legitimate operator
changes) are both visible in the Audit feed (B-1).
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

_TOKEN = "test-gateway-token"


def _runtime() -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token=_TOKEN,
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})
    return client


class _CapturingSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event, session_id=None, turn_id=None) -> None:
        self.events.append(dict(event))


def _install_sink(monkeypatch) -> _CapturingSink:
    from magi_agent.observability import runtime_sink

    sink = _CapturingSink()
    monkeypatch.setattr(runtime_sink, "_active_sink", sink)
    return sink


# --------------------------------------------------------------------------- #
# Allowlist GET/PUT                                                            #
# --------------------------------------------------------------------------- #
def test_get_allowlist_empty_default(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = client.get("/v1/app/customize/egress-allowlist")
    assert resp.status_code == 200
    assert resp.json()["allowlist"] == []


def test_put_allowlist_persists(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = client.put(
        "/v1/app/customize/egress-allowlist",
        json={"allowlist": ["api.github.com", "*.example.com"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["allowlist"] == ["api.github.com", "*.example.com"]
    cj = json.loads((tmp_path / "customize.json").read_text())
    assert cj["egress_guard"]["allowlist"] == ["api.github.com", "*.example.com"]


def test_put_allowlist_requires_auth(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = TestClient(create_app(_runtime()))  # no token
    resp = client.put(
        "/v1/app/customize/egress-allowlist", json={"allowlist": ["x.com"]}
    )
    assert resp.status_code == 401


def test_put_allowlist_bad_body(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = client.put("/v1/app/customize/egress-allowlist", json={"allowlist": "nope"})
    assert resp.status_code == 400


def test_put_allowlist_rejects_invalid_host(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = client.put(
        "/v1/app/customize/egress-allowlist",
        json={"allowlist": ["not a host", "https://x.com/path"]},
    )
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Config-change audit rows                                                     #
# --------------------------------------------------------------------------- #
def test_allowlist_change_emits_audit_row(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    # Install the capturing sink AFTER create_app (which registers the real
    # observability sink during app construction).
    sink = _install_sink(monkeypatch)
    client.put(
        "/v1/app/customize/egress-allowlist", json={"allowlist": ["api.github.com"]}
    )
    rows = [e for e in sink.events if e.get("surface") == "egress_allowlist"]
    assert rows, "allowlist write must emit a config-change audit row"
    row = rows[-1]
    assert row["policyId"] == "egress_guard"
    assert "beforeHash" in row and "afterHash" in row


def test_mode_change_emits_audit_row(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    sink = _install_sink(monkeypatch)
    resp = client.put(
        "/v1/app/customize/egress-mode", json={"mode": "block"}
    )
    assert resp.status_code == 200
    rows = [e for e in sink.events if e.get("surface") == "egress_mode"]
    assert rows
    assert rows[-1]["policyId"] == "egress_guard"


def test_builtin_policy_toggle_emits_audit_row(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    sink = _install_sink(monkeypatch)
    resp = client.patch(
        "/v1/app/customize/builtin-policies/egress_guard", json={"enabled": False}
    )
    assert resp.status_code == 200
    rows = [e for e in sink.events if e.get("surface") == "builtin_policy_toggle"]
    assert rows
    assert rows[-1]["policyId"] == "egress_guard"
