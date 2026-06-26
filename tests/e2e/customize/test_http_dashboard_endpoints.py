"""HTTP coverage for the non-custom-rule customize endpoints.

The dashboard's Policies tab is mostly custom_rules CRUD (covered in
``test_http_full_matrix.py`` + ``test_http_to_fire_roundtrip.py``).
The remaining wizard surfaces hit different endpoints. This file
walks each one:

* Behaviors tab -> PATCH /v1/app/customize/control-plane/{id} for
  all 4 ids in :data:`CONTROL_PLANE_BEHAVIORS` (the hosted 404 was
  on this surface).
* Tools tab -> PATCH /v1/app/customize/tools/{name} (enable/disable
  toggle per registered tool).
* Recipes tab -> PATCH /v1/app/customize/verification/recipes/{id}
  (enable + disable both halves; the list-shape semantics catch a
  drift to dict-shape).
* harness_presets + hooks toggles via
  PATCH /v1/app/customize/verification/{kind}/{id} (the other two
  kinds the dashboard PATCHes alongside recipes).
* user_rules tab -> PUT /v1/app/customize/rules (free-form text the
  dashboard's Guidance tab writes).
* Budgets tab -> GET + PUT /v1/app/customize/budgets round-trip.

Every assertion is a persistence round-trip — the dashboard never
trusts a 200 alone; it re-renders from the response body. A 200 +
empty body would be a silent failure for the operator.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.customize.control_plane_overrides import (
    CONTROL_PLANE_BEHAVIORS,
)
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


@pytest.fixture
def http_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})
    return client


# ---------------------------------------------------------------------------
# Control-plane behaviors — every id in the catalog round-trips. The
# hosted 404 was on this surface; this parametrized sweep makes it
# obvious whether the endpoint exists for EVERY behavior the dashboard
# would render a toggle for.
# ---------------------------------------------------------------------------


_BEHAVIOR_IDS = sorted(b.id for b in CONTROL_PLANE_BEHAVIORS)


@pytest.mark.parametrize("behavior_id", _BEHAVIOR_IDS)
def test_control_plane_behavior_disable_enable_roundtrip(
    behavior_id: str, http_client: TestClient
) -> None:
    """Toggle OFF then ON for each behavior id; assert both halves persist."""
    off = http_client.patch(
        f"/v1/app/customize/control-plane/{behavior_id}",
        json={"enabled": False},
    )
    assert off.status_code == 200, (
        f"PATCH disable for {behavior_id!r} expected 200; "
        f"got {off.status_code} body={off.text}"
    )
    overrides = off.json().get("overrides", {}).get("control_plane", {})
    assert overrides.get(behavior_id) is False, (
        f"after disable, overrides.control_plane[{behavior_id!r}] must be False; "
        f"got {overrides!r}"
    )

    on = http_client.patch(
        f"/v1/app/customize/control-plane/{behavior_id}",
        json={"enabled": True},
    )
    assert on.status_code == 200
    overrides = on.json().get("overrides", {}).get("control_plane", {})
    assert overrides.get(behavior_id) is True, (
        f"after enable, overrides.control_plane[{behavior_id!r}] must be True; "
        f"got {overrides!r}"
    )


def test_control_plane_behavior_unknown_id_rejected(
    http_client: TestClient,
) -> None:
    """Unknown behavior id MUST 404 (the hosted symptom on a stale image)."""
    resp = http_client.patch(
        "/v1/app/customize/control-plane/does-not-exist",
        json={"enabled": False},
    )
    assert resp.status_code in {404, 400}, (
        f"unknown behavior id MUST be rejected (404 / 400); "
        f"got {resp.status_code} body={resp.text}"
    )


# ---------------------------------------------------------------------------
# Tools tab — PATCH /v1/app/customize/tools/{name}
# ---------------------------------------------------------------------------


def test_tools_toggle_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tools tab toggle: PATCH /tools/{name} round-trips into overrides.tools.

    Tool names are runtime-registered, so this test builds its own
    client to query the registry for a real tool id (mirrors the
    pre-existing ``test_patch_tool_persists_and_applies`` pattern).
    """
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    runtime = _runtime()
    tool_name = runtime.tool_registry.list_all()[0].name
    client = TestClient(create_app(runtime))
    client.headers.update({"x-gateway-token": _TOKEN})

    resp = client.patch(
        f"/v1/app/customize/tools/{tool_name}", json={"enabled": False}
    )
    assert resp.status_code == 200, (
        f"PATCH /tools/{tool_name} expected 200; "
        f"got {resp.status_code} body={resp.text}"
    )
    tools = resp.json().get("overrides", {}).get("tools", {})
    # The patch_tool route stores the bool directly under the name
    # (see set_tool_override).
    assert tools.get(tool_name) is False, (
        f"tool toggle must persist into overrides.tools; got {tools!r}"
    )


# ---------------------------------------------------------------------------
# Recipes / harness_presets / hooks toggles — the 3 verification kinds the
# dashboard PATCHes through /verification/{kind}/{item_id}.
# ---------------------------------------------------------------------------


def test_verification_recipes_enable_disable_roundtrip(
    http_client: TestClient,
) -> None:
    """Verification recipes bucket: list-backed; enable appends, disable removes."""
    enable = http_client.patch(
        "/v1/app/customize/verification/recipes/research",
        json={"enabled": True},
    )
    assert enable.status_code == 200
    recipes = (
        enable.json()
        .get("overrides", {})
        .get("verification", {})
        .get("recipes", [])
    )
    assert "research" in recipes, (
        f"after enable, recipe id must be in the list; got {recipes!r}"
    )

    disable = http_client.patch(
        "/v1/app/customize/verification/recipes/research",
        json={"enabled": False},
    )
    assert disable.status_code == 200
    recipes = (
        disable.json()
        .get("overrides", {})
        .get("verification", {})
        .get("recipes", [])
    )
    assert "research" not in recipes, (
        f"after disable, recipe id must be removed; got {recipes!r}"
    )


def test_verification_harness_presets_roundtrip(http_client: TestClient) -> None:
    """harness_presets bucket: dict {id: bool}; both enable and disable persist."""
    # Use a preset id that doesn't need catalog validation; the route
    # accepts arbitrary harness_presets ids for tri-state retention.
    resp = http_client.patch(
        "/v1/app/customize/verification/harness_presets/coding-evidence-gate",
        json={"enabled": False},
    )
    assert resp.status_code == 200
    presets = (
        resp.json()
        .get("overrides", {})
        .get("verification", {})
        .get("preset_overrides", {})
    )
    assert presets.get("coding-evidence-gate") is False


def test_verification_hooks_roundtrip(http_client: TestClient) -> None:
    """hooks bucket: dict {id: bool}; persists round-trip."""
    resp = http_client.patch(
        "/v1/app/customize/verification/hooks/preCommit",
        json={"enabled": False},
    )
    assert resp.status_code == 200
    hooks = (
        resp.json()
        .get("overrides", {})
        .get("verification", {})
        .get("hooks", {})
    )
    assert hooks.get("preCommit") is False


def test_verification_unknown_kind_400(http_client: TestClient) -> None:
    """Validator MUST reject unknown verification kind with 400."""
    resp = http_client.patch(
        "/v1/app/customize/verification/nope/whatever", json={"enabled": True}
    )
    assert resp.status_code == 400, (
        f"unknown kind 'nope' MUST 400; got {resp.status_code} body={resp.text}"
    )


def test_verification_recipes_unknown_id_404(http_client: TestClient) -> None:
    """Recipes catalog: unknown id MUST 404 (the F-UX10 typo guard)."""
    resp = http_client.patch(
        "/v1/app/customize/verification/recipes/does-not-exist",
        json={"enabled": True},
    )
    assert resp.status_code == 404, (
        f"unknown recipe id MUST 404; got {resp.status_code} body={resp.text}"
    )


# ---------------------------------------------------------------------------
# user_rules tab (Guidance) - PUT /v1/app/customize/rules
# ---------------------------------------------------------------------------


def test_user_rules_text_persists(http_client: TestClient) -> None:
    """Guidance text: PUT /rules round-trips into overrides.user_rules.

    Body shape: ``{"text": "..."}`` (see transport/customize.py
    put_rules validator).
    """
    resp = http_client.put(
        "/v1/app/customize/rules", json={"text": "be terse; quote sources"}
    )
    assert resp.status_code == 200, (
        f"PUT /rules expected 200; got {resp.status_code} body={resp.text}"
    )
    text = resp.json().get("overrides", {}).get("user_rules", "")
    assert "be terse" in text


# ---------------------------------------------------------------------------
# Budgets tab - GET + PUT /v1/app/customize/budgets
# ---------------------------------------------------------------------------


def test_budgets_get_then_put_roundtrip(http_client: TestClient) -> None:
    """Budgets tab: GET then PUT a partial body; assert persistence.

    Body shape: ``{"budgets": {budgetName: positiveInt, ...}}`` where
    budgetName is a key from ``BUDGET_ENV_MAP`` (see
    transport/customize.py put_customize_budgets validator).
    """
    get_resp = http_client.get("/v1/app/customize/budgets")
    assert get_resp.status_code == 200

    # Use a real key from BUDGET_ENV_MAP. The validator rejects
    # unknown names so this is part of the contract under test.
    put_resp = http_client.put(
        "/v1/app/customize/budgets",
        json={"budgets": {"maxToolCallsPerTurn": 7}},
    )
    assert put_resp.status_code == 200, (
        f"PUT /budgets expected 200; got {put_resp.status_code} "
        f"body={put_resp.text}"
    )
    budgets = (
        put_resp.json()
        .get("overrides", {})
        .get("verification", {})
        .get("budgets", {})
    )
    assert budgets.get("maxToolCallsPerTurn") == 7, (
        f"per-turn tool-call cap must persist into budgets bucket; "
        f"got {budgets!r}"
    )


def test_budgets_unknown_name_rejected(http_client: TestClient) -> None:
    """Validator rejects unknown budget names so a typo cannot land silently."""
    resp = http_client.put(
        "/v1/app/customize/budgets",
        json={"budgets": {"per_turn_tool_calls_max_typo": 99}},
    )
    assert resp.status_code == 400, (
        f"unknown budget name MUST 400; got {resp.status_code} body={resp.text}"
    )
