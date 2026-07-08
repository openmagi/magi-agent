"""PR-1 U3: the /v1/app/customize catalog exposes a ``policies`` key.

Each entry is a serialized policy summary {id, displayName, intent, ruleIds,
origin, userDisableable, reviewVerdict, hasBinding, enabledState}. camelCase
keys mirror the rest of the catalog payload. The ``enabledState`` is derived
from the member custom rules' ``enabled`` flags (on/off/mixed).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.customize.catalog import build_catalog
from magi_agent.customize.policies import Policy, upsert_policy
from magi_agent.customize.store import set_custom_rule


class _FakeToolRegistry:
    def list_all(self) -> list:
        return []

    def resolve_registration(self, name: str):  # noqa: ANN001
        return None


class _FakeRuntime:
    def __init__(self) -> None:
        self.tool_registry = _FakeToolRegistry()


def _rule(rule_id: str, *, enabled: bool = True) -> dict:
    return {
        "id": rule_id,
        "scope": "always",
        "enabled": enabled,
        "what": {"kind": "deterministic_ref", "payload": {"ref": "evidence:test"}},
        "firesAt": "pre_final",
        "action": "block",
    }


def _use_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(p))
    return p


def test_catalog_has_policies_key_with_builtins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_store(tmp_path, monkeypatch)
    catalog = build_catalog(_FakeRuntime())
    assert "policies" in catalog
    ids = {p["id"] for p in catalog["policies"]}
    # First-party builtins are always present.
    assert {"source_citation", "verify_before_replying"} <= ids


def test_catalog_policy_entry_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _use_store(tmp_path, monkeypatch)
    set_custom_rule(_rule("cr_a"), path=p)
    upsert_policy(
        Policy.model_validate(
            {
                "id": "grp",
                "displayName": "My group",
                "intent": "keep outputs clean",
                "ruleIds": ["cr_a"],
            }
        ),
        p,
    )
    catalog = build_catalog(_FakeRuntime())
    entry = next(e for e in catalog["policies"] if e["id"] == "grp")
    assert entry["displayName"] == "My group"
    assert entry["intent"] == "keep outputs clean"
    assert entry["ruleIds"] == ["cr_a"]
    assert entry["origin"] == "user"
    assert entry["userDisableable"] is True
    assert "reviewVerdict" in entry
    assert entry["hasBinding"] is False
    assert entry["enabledState"] == "on"


def test_catalog_policy_enabled_state_mixed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _use_store(tmp_path, monkeypatch)
    set_custom_rule(_rule("cr_a", enabled=True), path=p)
    set_custom_rule(_rule("cr_b", enabled=False), path=p)
    upsert_policy(
        Policy.model_validate(
            {"id": "grp", "displayName": "Mixed", "ruleIds": ["cr_a", "cr_b"]}
        ),
        p,
    )
    catalog = build_catalog(_FakeRuntime())
    entry = next(e for e in catalog["policies"] if e["id"] == "grp")
    assert entry["enabledState"] == "mixed"


def test_catalog_policy_enabled_state_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _use_store(tmp_path, monkeypatch)
    set_custom_rule(_rule("cr_a", enabled=False), path=p)
    upsert_policy(
        Policy.model_validate(
            {"id": "grp", "displayName": "Off", "ruleIds": ["cr_a"]}
        ),
        p,
    )
    catalog = build_catalog(_FakeRuntime())
    entry = next(e for e in catalog["policies"] if e["id"] == "grp")
    assert entry["enabledState"] == "off"


def test_catalog_builtin_floor_marked_not_disableable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_store(tmp_path, monkeypatch)
    catalog = build_catalog(_FakeRuntime())
    floor = next(e for e in catalog["policies"] if e["id"] == "source_citation")
    assert floor["userDisableable"] is False
    assert floor["origin"] == "builtin"


def test_catalog_verify_before_replying_has_real_toggle_not_managed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR-3 honesty fix: ``verify_before_replying`` is a first-party policy the
    user CAN disable via the builtin-policies route. PR-2 reported it as
    ``managed`` (a static pill) because it has no stored custom-rule members —
    dishonest, since it has a real single toggle. It must now carry
    ``source="builtinPolicy"`` and an on/off ``enabledState`` (never
    ``managed``) so the web renders a working Switch routed to the
    builtin-policies PATCH."""
    _use_store(tmp_path, monkeypatch)
    # Default-ON verify flag (profile-aware default reported by the
    # builtin-policy catalog); assert the real on/off state, not ``managed``.
    catalog = build_catalog(_FakeRuntime())
    builtin = next(
        e for e in catalog["policies"] if e["id"] == "verify_before_replying"
    )
    assert builtin["source"] == "builtinPolicy"
    assert builtin["enabledState"] in {"on", "off"}
    assert builtin["userDisableable"] is True


def test_catalog_verify_before_replying_enabled_state_tracks_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The builtin-policy enabledState reflects the master env flag, so an
    explicit opt-out shows ``off``."""
    _use_store(tmp_path, monkeypatch)
    monkeypatch.setenv("MAGI_VERIFY_BEFORE_REPLYING_ENABLED", "0")
    catalog = build_catalog(_FakeRuntime())
    builtin = next(
        e for e in catalog["policies"] if e["id"] == "verify_before_replying"
    )
    assert builtin["enabledState"] == "off"
    monkeypatch.setenv("MAGI_VERIFY_BEFORE_REPLYING_ENABLED", "1")
    catalog = build_catalog(_FakeRuntime())
    builtin = next(
        e for e in catalog["policies"] if e["id"] == "verify_before_replying"
    )
    assert builtin["enabledState"] == "on"


def test_catalog_source_citation_floor_source_and_disableable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The floor keeps ``source="builtinPolicy"`` (a first-party policy) but
    stays ``userDisableable=false`` so the web renders it always-on."""
    _use_store(tmp_path, monkeypatch)
    catalog = build_catalog(_FakeRuntime())
    floor = next(e for e in catalog["policies"] if e["id"] == "source_citation")
    assert floor["source"] == "builtinPolicy"
    assert floor["userDisableable"] is False


def test_catalog_user_policy_has_policy_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _use_store(tmp_path, monkeypatch)
    set_custom_rule(_rule("cr_a"), path=p)
    upsert_policy(
        Policy.model_validate(
            {"id": "grp", "displayName": "G", "ruleIds": ["cr_a"]}
        ),
        p,
    )
    catalog = build_catalog(_FakeRuntime())
    entry = next(e for e in catalog["policies"] if e["id"] == "grp")
    assert entry["source"] == "policy"


def test_catalog_control_plane_behaviors_adapted_as_nudge_policies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR-3 / D4: the 4 control-plane behaviors surface as first-party 1-rule
    ``nudge`` policy cards with ``source="controlPlane"`` and no member rules."""
    _use_store(tmp_path, monkeypatch)
    catalog = build_catalog(_FakeRuntime())
    by_id = {e["id"]: e for e in catalog["policies"]}
    for behavior_id in (
        "facts-replan",
        "goal-loop",
        "tool-synthesis-nudge",
        "empty-response-recovery",
    ):
        entry = by_id[behavior_id]
        assert entry["source"] == "controlPlane"
        assert entry["actionHint"] == "nudge"
        assert entry["origin"] == "builtin"
        assert entry["ruleIds"] == []
        assert entry["hasBinding"] is False
        assert entry["userDisableable"] is True
        assert entry["enabledState"] in {"on", "off"}
        # intent = the behavior's own description; displayName = its label.
        assert entry["displayName"] and entry["intent"]


def test_catalog_control_plane_enabled_state_tracks_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A control-plane adapter card's on/off reflects the behavior's env flag."""
    _use_store(tmp_path, monkeypatch)
    monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
    catalog = build_catalog(_FakeRuntime())
    goal = next(e for e in catalog["policies"] if e["id"] == "goal-loop")
    assert goal["enabledState"] == "on"
    monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "0")
    catalog = build_catalog(_FakeRuntime())
    goal = next(e for e in catalog["policies"] if e["id"] == "goal-loop")
    assert goal["enabledState"] == "off"


def test_catalog_user_policy_without_stored_members_is_managed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _use_store(tmp_path, monkeypatch)
    upsert_policy(
        Policy.model_validate(
            {
                "id": "producer-only",
                "displayName": "Producer only",
                "ruleIds": ["dash_check_xyz"],  # not a stored custom rule
            }
        ),
        p,
    )
    catalog = build_catalog(_FakeRuntime())
    entry = next(e for e in catalog["policies"] if e["id"] == "producer-only")
    assert entry["enabledState"] == "managed"
