"""PR-P3: installed-pack inventory (read-only Packs contents view).

Hermetic: monkeypatches the search bases to the bundled first-party dir only,
so the test does not pick up a developer's ~/.magi/packs or cwd/.magi/packs.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import magi_agent.packs.inventory as inventory
from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.packs.discovery import _bundled_firstparty_base
from magi_agent.packs.inventory import installed_packs_view
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

_TOKEN = "test-gateway-token"


@pytest.fixture(autouse=True)
def _bundled_only(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only the in-repo bundled base → deterministic first-party set.
    monkeypatch.setattr(
        inventory, "default_search_bases", lambda: [_bundled_firstparty_base()]
    )


def test_view_lists_bundled_first_party_packs_with_provides() -> None:
    packs = installed_packs_view()
    assert packs, "expected bundled first-party packs to be discovered"
    by_id = {p["packId"]: p for p in packs}
    # A known bundled pack with multiple provides.
    activity = by_id.get("openmagi.evidence-firstparty-activity")
    assert activity is not None
    assert activity["origin"] == "first_party"
    assert activity["provides"], "pack must expose what it provides"
    entry = activity["provides"][0]
    assert set(entry) == {"type", "ref"}
    assert entry["type"] == "evidence_producer"


def test_view_is_sorted_first_party_before_user_then_alpha() -> None:
    packs = installed_packs_view()
    origins = [p["origin"] for p in packs]
    # All bundled here, so every entry is first_party and ids are alphabetical.
    assert origins == ["first_party"] * len(packs)
    ids = [p["packId"] for p in packs]
    assert ids == sorted(ids)


def test_every_entry_has_the_documented_shape() -> None:
    for p in installed_packs_view():
        assert set(p) == {
            "packId",
            "displayName",
            "description",
            "version",
            "origin",
            "defaultEnabled",
            "enabled",
            "provides",
        }
        assert isinstance(p["enabled"], bool)
        assert isinstance(p["defaultEnabled"], bool)


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


def test_route_requires_auth() -> None:
    client = TestClient(create_app(_runtime()))
    assert client.get("/v1/app/packs").status_code == 401


def test_route_returns_pack_inventory() -> None:
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})
    resp = client.get("/v1/app/packs")
    assert resp.status_code == 200
    packs = resp.json()["packs"]
    assert any(p["packId"] == "openmagi.evidence-firstparty-activity" for p in packs)
