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


def test_catalog_builtin_enabled_state_is_managed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A policy with NO stored custom-rule members has nothing on the per-rule
    enabled axis; it must report ``managed``, not a fake ``on`` (review
    finding: a green toggle the user cannot move is dishonest)."""
    _use_store(tmp_path, monkeypatch)
    catalog = build_catalog(_FakeRuntime())
    builtin = next(
        e for e in catalog["policies"] if e["id"] == "verify_before_replying"
    )
    assert builtin["enabledState"] == "managed"


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
