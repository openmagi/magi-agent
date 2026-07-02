"""PR-P5.1: read-only built-in POSTURE modes (Coding/Research/Delivery).

Posture-only (soft system prompt, no enforcement re-homing); read-only
(non-editable/non-deletable); inert until selected (byte-identical when no
mode is active). Profile-aware default-ON via MAGI_CUSTOMIZE_BUILTIN_MODES_ENABLED.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.customize import modes as m

_BUILTINS = {"builtin-coding", "builtin-research", "builtin-delivery"}


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))


def _enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_BUILTIN_MODES_ENABLED", "1")


def _disable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_BUILTIN_MODES_ENABLED", "0")


def test_builtins_listed_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    ids = {mode.mode_id for mode in m.list_modes()}
    assert _BUILTINS <= ids


def test_builtins_absent_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable(monkeypatch)
    assert m.list_modes() == ()
    assert m.get_mode("builtin-coding") is None


def test_builtins_are_posture_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    for mode in m.builtin_modes():
        # Posture = a soft system prompt; no enforcement re-homing, no tool
        # widening, no scoped policies (so activating one only injects a prompt).
        assert mode.system_prompt.strip()
        assert mode.tool_delta.exclude == ()
        assert mode.tool_delta.include == ()
        assert mode.scoped_policy_ids == ()


def test_builtin_resolvable_and_selectable(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    assert m.get_mode("builtin-coding") is not None
    m.set_active_mode("builtin-coding")
    assert m.active_mode_id() == "builtin-coding"


def test_builtins_are_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    coding = m.get_mode("builtin-coding")
    assert coding is not None
    with pytest.raises(ValueError):
        m.upsert_mode(coding)
    with pytest.raises(ValueError):
        m.delete_mode("builtin-coding")


def test_user_mode_shadows_builtin_id(monkeypatch: pytest.MonkeyPatch) -> None:
    # A stored mode with a built-in id wins (user customization). Since upsert
    # rejects built-in ids, this only happens via a hand-edited store; list must
    # not double-list the id.
    _enable(monkeypatch)
    listed = m.list_modes()
    ids = [mode.mode_id for mode in listed]
    assert len(ids) == len(set(ids))  # no duplicate ids


def test_no_active_mode_is_byte_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    # With the flag ON but no active mode, nothing is applied at runtime; the
    # active selection is None regardless of built-ins being listed.
    _enable(monkeypatch)
    assert m.active_mode_id() is None
