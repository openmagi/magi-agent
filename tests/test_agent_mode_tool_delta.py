from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from magi_agent.cli.wiring import (
    _agent_mode_excluded_tool_names,
    _agent_mode_included_tool_names,
    _mode_include_allows_manifest,
)
from magi_agent.customize.modes import AgentMode, set_active_mode, upsert_mode
from magi_agent.runtime.per_turn_agent_mode_context import (
    reset_per_turn_agent_mode,
    set_per_turn_agent_mode,
)


@pytest.fixture
def customize_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))


def _mode(mode_id: str, exclude: list[str]) -> AgentMode:
    return AgentMode.model_validate(
        {
            "id": mode_id,
            "displayName": mode_id.title(),
            "toolDelta": {"exclude": exclude, "include": []},
        }
    )


def test_no_mode_no_exclusions(customize_env: None) -> None:
    assert _agent_mode_excluded_tool_names() == frozenset()


def test_active_mode_exclusions(customize_env: None) -> None:
    upsert_mode(_mode("review", ["FileEdit", "Bash"]))
    set_active_mode("review")
    assert _agent_mode_excluded_tool_names() == frozenset({"FileEdit", "Bash"})


def test_per_turn_override_wins(customize_env: None) -> None:
    upsert_mode(_mode("review", ["FileEdit"]))
    upsert_mode(_mode("coding", []))
    set_active_mode("coding")  # stored active = coding (no exclusions)
    token = set_per_turn_agent_mode("review")  # per-turn override -> review
    try:
        assert _agent_mode_excluded_tool_names() == frozenset({"FileEdit"})
    finally:
        reset_per_turn_agent_mode(token)


def test_unknown_mode_empty(customize_env: None) -> None:
    upsert_mode(_mode("review", ["FileEdit"]))
    set_active_mode("review")
    token = set_per_turn_agent_mode("nonexistent")
    try:
        assert _agent_mode_excluded_tool_names() == frozenset()
    finally:
        reset_per_turn_agent_mode(token)


def test_include_does_not_contribute_to_excluded_set(customize_env: None) -> None:
    # include and exclude are disjoint halves: a mode's `include` must never
    # leak into the EXCLUDED set (it is applied separately via
    # `_agent_mode_included_tool_names`).
    upsert_mode(
        AgentMode.model_validate(
            {
                "id": "grant",
                "displayName": "Grant",
                "toolDelta": {"exclude": [], "include": ["SomeDefaultOffTool"]},
            }
        )
    )
    set_active_mode("grant")
    assert _agent_mode_excluded_tool_names() == frozenset()


# --- include hard-safety cap (PR-A) ------------------------------------------


def _fake_manifest(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "available_in_modes": ("plan", "act"),
        "dangerous": False,
        "permission": "read",
        "side_effect_class": "none",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeRegistry:
    """Minimal registry exposing only what the include resolver reads."""

    def __init__(self, tools: dict[str, tuple[object, object]]) -> None:
        # name -> (manifest, handler); handler=None models a handler-less tool.
        self._tools = tools
        self.enabled: list[str] = []

    def resolve_registration(self, name: str) -> object | None:
        entry = self._tools.get(name)
        if entry is None:
            return None
        manifest, handler = entry
        return SimpleNamespace(manifest=manifest, handler=handler)

    def enable(self, name: str) -> None:
        self.enabled.append(name)


@pytest.mark.parametrize(
    "overrides,allowed",
    [
        ({}, True),  # benign read tool
        ({"dangerous": True}, False),
        ({"permission": "execute"}, False),
        ({"permission": "net"}, False),
        ({"permission": "computer"}, False),
        ({"permission": "meta"}, False),  # tool/runtime mgmt — unbounded blast radius
        ({"side_effect_class": "external"}, False),
        ({"side_effect_class": "local_and_external"}, False),
        ({"side_effect_class": "local_process"}, False),  # process-spawning refused
        ({"available_in_modes": ("plan",)}, False),  # not available in act
        ({"permission": "write"}, True),  # local write is allowed
        ({"side_effect_class": "local_workspace"}, True),
    ],
)
def test_include_cap_predicate(overrides: dict[str, object], allowed: bool) -> None:
    assert (
        _mode_include_allows_manifest(_fake_manifest(**overrides), mode="act") is allowed
    )


def test_include_cap_allowlist_refuses_unknown_future_class() -> None:
    # Allowlist semantics: a permission / side-effect value introduced after
    # this code (not in the allowlist) must fail closed.
    assert _mode_include_allows_manifest(
        _fake_manifest(permission="quantum"), mode="act"
    ) is False
    assert _mode_include_allows_manifest(
        _fake_manifest(side_effect_class="warp"), mode="act"
    ) is False


def test_include_cap_fails_closed_on_partial_manifest() -> None:
    # A manifest missing the safety-relevant attributes must be refused, never
    # silently admitted.
    assert _mode_include_allows_manifest(SimpleNamespace(), mode="act") is False


def _include_mode(mode_id: str, include: list[str], exclude: list[str] | None = None) -> AgentMode:
    return AgentMode.model_validate(
        {
            "id": mode_id,
            "displayName": mode_id.title(),
            "toolDelta": {"include": include, "exclude": exclude or []},
        }
    )


def test_include_admits_benign_registered_tool(customize_env: None) -> None:
    registry = _FakeRegistry({"Formatter": (_fake_manifest(), object())})
    upsert_mode(_include_mode("tidy", ["Formatter"]))
    set_active_mode("tidy")
    assert _agent_mode_included_tool_names(registry, mode="act") == frozenset({"Formatter"})


def test_include_refuses_dangerous_tool(customize_env: None) -> None:
    registry = _FakeRegistry(
        {"Bash": (_fake_manifest(dangerous=True, permission="execute"), object())}
    )
    upsert_mode(_include_mode("power", ["Bash"]))
    set_active_mode("power")
    assert _agent_mode_included_tool_names(registry, mode="act") == frozenset()


def test_include_exclude_wins_for_same_name(customize_env: None) -> None:
    registry = _FakeRegistry({"Formatter": (_fake_manifest(), object())})
    upsert_mode(_include_mode("conflict", ["Formatter"], exclude=["Formatter"]))
    set_active_mode("conflict")
    assert _agent_mode_included_tool_names(registry, mode="act") == frozenset()


def test_include_drops_unknown_and_handlerless(customize_env: None) -> None:
    registry = _FakeRegistry(
        {"NoHandler": (_fake_manifest(), None)}  # registered but handler-less
    )
    upsert_mode(_include_mode("ghost", ["NoHandler", "Unregistered"]))
    set_active_mode("ghost")
    assert _agent_mode_included_tool_names(registry, mode="act") == frozenset()


def test_include_empty_when_no_active_mode(customize_env: None) -> None:
    registry = _FakeRegistry({"Formatter": (_fake_manifest(), object())})
    assert _agent_mode_included_tool_names(registry, mode="act") == frozenset()


def test_include_per_turn_override_wins(customize_env: None) -> None:
    registry = _FakeRegistry({"Formatter": (_fake_manifest(), object())})
    upsert_mode(_include_mode("plain", []))
    upsert_mode(_include_mode("tidy", ["Formatter"]))
    set_active_mode("plain")  # sticky = no includes
    token = set_per_turn_agent_mode("tidy")  # per-turn wins
    try:
        assert _agent_mode_included_tool_names(registry, mode="act") == frozenset(
            {"Formatter"}
        )
    finally:
        reset_per_turn_agent_mode(token)


# --- real-catalog integration (cap holds on real manifests; loop is wired) ---


def _real_core_registry() -> object:
    from magi_agent.runtime.openmagi_runtime import (
        _build_core_tool_registry,
        _build_default_plugin_state,
    )

    return _build_core_tool_registry(_build_default_plugin_state())


def test_include_cap_never_admits_dangerous_from_real_catalog(customize_env: None) -> None:
    # Include EVERY tool the real core registry knows about, then assert the cap
    # admitted none that violate the hard-safety invariant. Proves the real
    # Bash/TestRun/PythonExec/ComputerTask manifests carry metadata the cap
    # actually refuses — not just the fabricated fakes above.
    registry = _real_core_registry()
    all_names = [m.name for m in registry.list_all()]
    assert "Bash" in all_names  # sanity: the dangerous exemplar is in the catalog
    upsert_mode(_include_mode("everything", all_names))
    set_active_mode("everything")
    admitted = _agent_mode_included_tool_names(registry, mode="act")
    assert "Bash" not in admitted
    for name in admitted:
        manifest = registry.resolve_registration(name).manifest
        assert manifest.dangerous is False
        assert manifest.permission in {"read", "write"}
        assert manifest.side_effect_class in {"none", "local_workspace"}
        assert "act" in manifest.available_in_modes


def test_include_loop_widens_built_toolset(
    customize_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # End-to-end: prove the enable loop in `_build_first_party_adk_tools` is
    # actually wired — an admissible default-OFF tool absent from the baseline
    # build appears once an include-mode names it. Deleting the loop fails this.
    from magi_agent.cli import wiring as wiring_mod

    monkeypatch.setenv("MAGI_FIRST_PARTY_TOOLS_ENABLED", "1")
    discovery = _real_core_registry()
    candidate: str | None = None
    for manifest in discovery.list_all():
        if not discovery.is_enabled(
            manifest.name
        ) and wiring_mod._mode_include_allows_manifest(manifest, mode="act"):
            candidate = manifest.name
            break
    if candidate is None:
        pytest.skip("no default-off admissible tool in the core registry")

    def _built_names() -> set[str | None]:
        return {
            getattr(tool, "name", None)
            for tool in wiring_mod._build_first_party_adk_tools(
                cwd=str(tmp_path), session_id="s"
            )
        }

    baseline = _built_names()
    if candidate in baseline:
        pytest.skip(f"candidate {candidate!r} already exposed in baseline build")

    upsert_mode(_include_mode("widen", [candidate]))
    set_active_mode("widen")
    assert candidate in _built_names()
