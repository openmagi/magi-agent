"""PR-F7 round-trip persistence tests for Customize budgets.

The dashboard's PUT /v1/app/customize/budgets path writes through
:func:`set_verification_budgets`. The applier reads the result back via
:func:`load_overrides` -> :class:`CustomizeVerificationPolicy.from_overrides`.
This test asserts the two halves agree on the schema (additive, back-compat)
and that the on-disk normalization drops malformed values.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from magi_agent.customize.store import (
    DEFAULT_OVERRIDES,
    load_overrides,
    save_overrides,
    set_verification_budgets,
)
from magi_agent.customize.verification_policy import CustomizeVerificationPolicy


def test_default_overrides_includes_empty_budgets() -> None:
    """Schema: ``verification.budgets`` is an empty dict by default."""
    assert "budgets" in DEFAULT_OVERRIDES["verification"]
    assert DEFAULT_OVERRIDES["verification"]["budgets"] == {}


def test_set_verification_budgets_round_trip(tmp_path: Path) -> None:
    """Write then read: persisted budgets survive a save/load cycle."""
    cfile = tmp_path / "customize.json"
    set_verification_budgets(
        {"maxToolCallsPerTurn": 30, "loopGuardHardThreshold": 12},
        path=cfile,
    )
    loaded = load_overrides(cfile)
    assert loaded["verification"]["budgets"] == {
        "maxToolCallsPerTurn": 30,
        "loopGuardHardThreshold": 12,
    }


def test_policy_load_surfaces_budgets(tmp_path: Path) -> None:
    """The resolved policy view exposes the persisted budgets verbatim."""
    cfile = tmp_path / "customize.json"
    set_verification_budgets(
        {"maxToolCallsPerTurn": 30, "maxStepsBrakeHard": 50}, path=cfile
    )
    overrides = load_overrides(cfile)
    policy = CustomizeVerificationPolicy.from_overrides(overrides)
    assert policy.budget("maxToolCallsPerTurn") == 30
    assert policy.budget("maxStepsBrakeHard") == 50
    assert policy.budget("loopGuardHardThreshold") is None


def test_set_verification_budgets_drops_malformed_values(tmp_path: Path) -> None:
    """The setter sanitizes: non-positive / boolean / non-int values are dropped."""
    cfile = tmp_path / "customize.json"
    set_verification_budgets(
        {
            "maxToolCallsPerTurn": 30,
            "loopGuardHardThreshold": 0,  # zero
            "maxStepsBrakeHard": -1,  # negative
            "extraKey": True,  # boolean
            "anotherKey": "5",  # string
        },
        path=cfile,
    )
    loaded = load_overrides(cfile)
    assert loaded["verification"]["budgets"] == {"maxToolCallsPerTurn": 30}


def test_load_overrides_backcompat_no_budgets_key(tmp_path: Path) -> None:
    """Legacy customize.json without a ``budgets`` key loads as empty dict."""
    cfile = tmp_path / "customize.json"
    legacy = {
        "verification": {
            "recipes": [],
            "harness_presets": [],
            "preset_overrides": {},
            "hooks": {},
            "modes": {},
            "custom_rules": [],
            "seam_specs": [],
            # NOTE: no "budgets" key, mirroring pre-F7 files on disk.
        },
        "tools": {},
        "user_rules": "",
        "control_plane": {},
    }
    cfile.write_text(json.dumps(legacy), encoding="utf-8")
    loaded = load_overrides(cfile)
    assert loaded["verification"]["budgets"] == {}


def test_load_overrides_drops_malformed_budgets_on_disk(tmp_path: Path) -> None:
    """A hand-edited customize.json with garbage budgets is normalized on load.

    JSON only carries string keys, so the value-shape filter is what protects
    the runtime applier: anything that is not a positive int (no booleans, no
    strings, no negatives, no zero) must be dropped on load.
    """
    cfile = tmp_path / "customize.json"
    raw = {
        "verification": {
            "recipes": [],
            "harness_presets": [],
            "preset_overrides": {},
            "hooks": {},
            "modes": {},
            "custom_rules": [],
            "seam_specs": [],
            "budgets": {
                "maxToolCallsPerTurn": 42,
                "maxStepsBrakeHard": "oops",
                "loopGuardHardThreshold": -7,
                "zero": 0,
                "bool": True,
                "float": 1.5,
            },
        },
        "tools": {},
        "user_rules": "",
        "control_plane": {},
    }
    cfile.write_text(json.dumps(raw), encoding="utf-8")
    loaded = load_overrides(cfile)
    assert loaded["verification"]["budgets"] == {"maxToolCallsPerTurn": 42}


def test_round_trip_save_preserves_other_sections(tmp_path: Path) -> None:
    """Writing budgets does not clobber unrelated sections (user_rules, tools)."""
    cfile = tmp_path / "customize.json"
    save_overrides(
        {
            "verification": {
                "recipes": [],
                "harness_presets": [],
                "preset_overrides": {},
                "hooks": {},
                "modes": {},
                "custom_rules": [],
                "seam_specs": [],
                "budgets": {},
            },
            "tools": {"shell_exec": False},
            "user_rules": "hello",
            "control_plane": {"foo": True},
        },
        path=cfile,
    )
    set_verification_budgets({"maxToolCallsPerTurn": 30}, path=cfile)
    loaded = load_overrides(cfile)
    assert loaded["verification"]["budgets"] == {"maxToolCallsPerTurn": 30}
    assert loaded["tools"] == {"shell_exec": False}
    assert loaded["user_rules"] == "hello"
    assert loaded["control_plane"] == {"foo": True}


@pytest.mark.parametrize(
    "good_key",
    ["maxToolCallsPerTurn", "maxStepsBrakeHard", "loopGuardHardThreshold"],
)
def test_each_budget_key_round_trips(tmp_path: Path, good_key: str) -> None:
    """Each canonical F7 budget key is preserved through save+load."""
    cfile = tmp_path / "customize.json"
    set_verification_budgets({good_key: 7}, path=cfile)
    loaded = load_overrides(cfile)
    assert loaded["verification"]["budgets"] == {good_key: 7}
