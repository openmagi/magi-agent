"""Tests for apply_tool_overrides."""
from __future__ import annotations

from magi_agent.customize.apply import apply_tool_overrides, apply_verification_overrides


class _Reg:
    def __init__(self, names):
        self.enabled = {n: True for n in names}

    def resolve_registration(self, name):
        return object() if name in self.enabled else None

    def enable(self, name):
        self.enabled[name] = True

    def disable(self, name):
        self.enabled[name] = False


class _RT:
    def __init__(self, names):
        self.tool_registry = _Reg(names)


def test_apply_disables_and_enables_known_tools():
    rt = _RT(["a", "b", "c"])
    apply_tool_overrides(rt, {"tools": {"a": False, "b": True}})
    assert rt.tool_registry.enabled == {"a": False, "b": True, "c": True}


def test_apply_skips_unknown_tools():
    rt = _RT(["a"])
    apply_tool_overrides(rt, {"tools": {"ghost": False}})  # must not raise
    assert rt.tool_registry.enabled == {"a": True}


def test_apply_tolerates_missing_tools_key():
    rt = _RT(["a"])
    apply_tool_overrides(rt, {})  # must not raise
    assert rt.tool_registry.enabled == {"a": True}


class _Bare:
    pass


def test_apply_verification_noop_when_flag_off(monkeypatch):
    # Profile-aware default-ON, so OFF is explicit "0".
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    rt = _Bare()
    apply_verification_overrides(
        rt, {"verification": {"harness_presets": ["answer_quality"]}}
    )
    assert not hasattr(rt, "customize_verification_policy")


def test_apply_verification_sets_policy_when_flag_on(monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    rt = _Bare()
    apply_verification_overrides(
        rt,
        {"verification": {"harness_presets": ["answer_quality"], "modes": {"answer_quality": "llm"}}},
    )
    assert rt.customize_verification_policy.is_enabled("answer_quality")
    assert rt.customize_verification_policy.mode("answer_quality") == "llm"


def test_apply_verification_tolerates_bad_input(monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    rt = _Bare()
    apply_verification_overrides(rt, None)  # must not raise
    assert rt.customize_verification_policy.enabled_presets == frozenset()
