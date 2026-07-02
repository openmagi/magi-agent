from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.customize.modes import (
    AgentMode,
    ToolDelta,
    active_mode_id,
    delete_mode,
    get_mode,
    list_modes,
    set_active_mode,
    upsert_mode,
)


@pytest.fixture(autouse=True)
def _no_builtins(monkeypatch: pytest.MonkeyPatch) -> None:
    # These are user-store CRUD tests; isolate them from the default-ON built-in
    # posture modes (covered by tests/test_builtin_modes.py).
    monkeypatch.setenv("MAGI_CUSTOMIZE_BUILTIN_MODES_ENABLED", "0")


def _path(tmp_path: Path) -> Path:
    return tmp_path / "customize.json"


def _mode(mode_id: str = "coding", **kw) -> AgentMode:
    return AgentMode.model_validate(
        {
            "id": mode_id,
            "displayName": kw.get("displayName", "Coding"),
            "systemPrompt": kw.get("systemPrompt", "Be a careful engineer."),
            "toolDelta": kw.get("toolDelta", {"exclude": ["WebSearch"], "include": ["PatchApply"]}),
            "scopedPolicyIds": kw.get("scopedPolicyIds", ["verifier:sourceOpened@1"]),
        }
    )


def test_empty_store_has_no_modes(tmp_path: Path) -> None:
    p = _path(tmp_path)
    assert list_modes(p) == ()
    assert active_mode_id(p) is None
    assert get_mode("coding", p) is None


def test_upsert_get_roundtrip(tmp_path: Path) -> None:
    p = _path(tmp_path)
    upsert_mode(_mode(), p)
    got = get_mode("coding", p)
    assert got is not None
    assert got.mode_id == "coding"
    assert got.display_name == "Coding"
    assert got.system_prompt == "Be a careful engineer."
    assert got.tool_delta.exclude == ("WebSearch",)
    assert got.tool_delta.include == ("PatchApply",)
    assert got.scoped_policy_ids == ("verifier:sourceOpened@1",)


def test_list_modes_sorted_and_skips_malformed(tmp_path: Path) -> None:
    p = _path(tmp_path)
    upsert_mode(_mode("research", displayName="Research"), p)
    upsert_mode(_mode("coding"), p)
    # inject a malformed raw entry directly
    from magi_agent.customize.store import load_overrides, save_overrides

    ov = load_overrides(p)
    ov["agent_modes"]["broken"] = {"id": "broken", "displayName": ""}  # empty name -> invalid
    save_overrides(ov, p)

    ids = [m.mode_id for m in list_modes(p)]
    assert ids == ["coding", "research"]  # sorted, malformed skipped


def test_upsert_overwrites(tmp_path: Path) -> None:
    p = _path(tmp_path)
    upsert_mode(_mode(displayName="Coding"), p)
    upsert_mode(_mode(displayName="Coding v2"), p)
    got = get_mode("coding", p)
    assert got is not None and got.display_name == "Coding v2"
    assert len(list_modes(p)) == 1


def test_active_mode_set_get_clear(tmp_path: Path) -> None:
    p = _path(tmp_path)
    upsert_mode(_mode(), p)
    set_active_mode("coding", p)
    assert active_mode_id(p) == "coding"
    set_active_mode(None, p)
    assert active_mode_id(p) is None


def test_set_active_unknown_raises(tmp_path: Path) -> None:
    p = _path(tmp_path)
    with pytest.raises(ValueError):
        set_active_mode("does-not-exist", p)


def test_delete_clears_active(tmp_path: Path) -> None:
    p = _path(tmp_path)
    upsert_mode(_mode(), p)
    set_active_mode("coding", p)
    delete_mode("coding", p)
    assert get_mode("coding", p) is None
    assert active_mode_id(p) is None  # active cleared on delete


def test_delete_nonexistent_is_noop(tmp_path: Path) -> None:
    p = _path(tmp_path)
    upsert_mode(_mode(), p)
    delete_mode("nope", p)
    assert get_mode("coding", p) is not None


def test_validation_rejects_bad_id() -> None:
    with pytest.raises(ValidationError):
        AgentMode.model_validate({"id": "Bad ID", "displayName": "x"})


def test_validation_rejects_empty_display() -> None:
    with pytest.raises(ValidationError):
        AgentMode.model_validate({"id": "ok", "displayName": "   "})


def test_validation_rejects_bad_tool_name() -> None:
    with pytest.raises(ValidationError):
        ToolDelta.model_validate({"exclude": ["bad tool name!!"]})


def test_tool_delta_dedupes() -> None:
    delta = ToolDelta.model_validate({"exclude": ["A", "A", "B"], "include": []})
    assert delta.exclude == ("A", "B")


def test_default_overrides_gains_modes_keys() -> None:
    from magi_agent.customize.store import DEFAULT_OVERRIDES

    assert DEFAULT_OVERRIDES["agent_modes"] == {}
    assert DEFAULT_OVERRIDES["active_agent_mode"] is None
