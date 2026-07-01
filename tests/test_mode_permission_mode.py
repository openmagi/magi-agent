"""Tests for a mode's ``permission_mode`` field + tighten-only resolution."""
from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.customize.modes import (
    AgentMode,
    active_permission_mode,
    capped_permission_mode,
    set_active_mode,
    upsert_mode,
)
from magi_agent.runtime.per_turn_agent_mode_context import (
    reset_per_turn_agent_mode,
    set_per_turn_agent_mode,
)


@pytest.fixture
def customize_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))


def _mode(mode_id: str, permission_mode: str | None) -> AgentMode:
    payload: dict = {"id": mode_id, "displayName": mode_id.title()}
    if permission_mode is not None:
        payload["permissionMode"] = permission_mode
    return AgentMode.model_validate(payload)


# --- model validation --------------------------------------------------------


def test_permission_mode_defaults_none():
    m = AgentMode.model_validate({"id": "m", "displayName": "M"})
    assert m.permission_mode is None
    assert "permissionMode" in m.to_payload()  # round-trips (by alias)


def test_valid_permission_modes_accepted():
    for pm in ("default", "acceptEdits", "bypassPermissions", "smartApprove"):
        assert _mode("m", pm).permission_mode == pm


def test_invalid_permission_mode_rejected():
    with pytest.raises(ValueError, match="permissionMode"):
        AgentMode.model_validate({"id": "m", "displayName": "M", "permissionMode": "yolo"})


def test_permission_mode_round_trips_through_payload():
    m = _mode("m", "smartApprove")
    assert m.to_payload()["permissionMode"] == "smartApprove"
    assert AgentMode.model_validate(m.to_payload()).permission_mode == "smartApprove"


# --- capped_permission_mode (tighten-only) -----------------------------------


@pytest.mark.parametrize(
    "mode_value,baseline,expected",
    [
        (None, "bypassPermissions", "bypassPermissions"),  # unset → baseline
        ("default", "bypassPermissions", "default"),  # tighten from YOLO
        ("smartApprove", "bypassPermissions", "smartApprove"),  # tighten
        ("acceptEdits", "bypassPermissions", "acceptEdits"),  # tighten
        ("bypassPermissions", "default", "default"),  # LOOSEN refused
        ("acceptEdits", "default", "default"),  # loosen refused
        ("default", "default", "default"),  # equal → baseline
        ("nope", "default", "default"),  # invalid → baseline
        ("default", "weird-baseline", "weird-baseline"),  # unknown baseline → never override
    ],
)
def test_capped_permission_mode(mode_value, baseline, expected):
    assert capped_permission_mode(mode_value, baseline) == expected


# --- active_permission_mode resolution ---------------------------------------


def test_active_permission_mode_no_mode(customize_env: None) -> None:
    assert active_permission_mode() is None


def test_active_permission_mode_from_active_mode(customize_env: None) -> None:
    upsert_mode(_mode("review", "default"))
    set_active_mode("review")
    assert active_permission_mode() == "default"


def test_active_permission_mode_none_when_mode_sets_none(customize_env: None) -> None:
    upsert_mode(_mode("plain", None))
    set_active_mode("plain")
    assert active_permission_mode() is None


def test_active_permission_mode_per_turn_override_wins(customize_env: None) -> None:
    upsert_mode(_mode("plain", None))
    upsert_mode(_mode("review", "smartApprove"))
    set_active_mode("plain")
    token = set_per_turn_agent_mode("review")
    try:
        assert active_permission_mode() == "smartApprove"
    finally:
        reset_per_turn_agent_mode(token)


def test_permission_mode_set_matches_runtime_source_of_truth():
    # Guard against drift: the mode's accepted permission-mode set must equal the
    # runtime's canonical PermissionMode literal. If cli.permissions adds a mode,
    # this fails so the rank map is updated (drift direction is fail-safe:
    # unknown → baseline, never a loosen, but keep them in sync).
    import typing

    from magi_agent.cli.permissions import PermissionMode
    from magi_agent.customize.modes import _VALID_PERMISSION_MODES

    assert set(typing.get_args(PermissionMode)) == set(_VALID_PERMISSION_MODES)
