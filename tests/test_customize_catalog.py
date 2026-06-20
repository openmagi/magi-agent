"""Tests for magi_agent.customize.catalog.

Hook entries are sourced from the same fixed _RUNTIME_HOOK_POINTS tuple that
/v1/app/skills uses — OpenMagiRuntime has no hook_registry attribute.

Tool fakes MIRROR the real registry shapes:
- tool_registry.list_all() → list[ToolManifest-shaped objects]
  Fields: name, description, source (object with .kind), dangerous, enabled_by_default
  live enabled comes from tool_registry.resolve_registration(name).enabled
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from magi_agent.customize.catalog import (
    HARNESS_PRESETS,
    RECIPES,
    build_catalog,
)
from magi_agent.hooks.manifest import HookPoint
from magi_agent.transport.app_api import _RUNTIME_HOOK_POINTS


# ---------------------------------------------------------------------------
# Tool fakes — matches what tool_registry.list_all() / resolve_registration()
# returns on the real ToolRegistry.
# ---------------------------------------------------------------------------
class _FakeToolSource:
    def __init__(self, kind: str = "builtin") -> None:
        self.kind = kind


class _FakeToolManifest:
    def __init__(
        self,
        name: str,
        description: str = "",
        source_kind: str = "builtin",
        dangerous: bool = False,
        enabled_by_default: bool = False,
    ) -> None:
        self.name = name
        self.description = description
        self.source = _FakeToolSource(source_kind)
        self.dangerous = dangerous
        self.enabled_by_default = enabled_by_default


class _FakeToolRegistration:
    def __init__(self, manifest: _FakeToolManifest, enabled: bool) -> None:
        self.manifest = manifest
        self.enabled = enabled


class _FakeToolRegistry:
    def __init__(self, items: list[tuple[_FakeToolManifest, bool]]) -> None:
        self._manifests = [m for m, _ in items]
        self._registrations = {
            m.name: _FakeToolRegistration(m, enabled) for m, enabled in items
        }

    def list_all(self) -> list[_FakeToolManifest]:
        return list(self._manifests)

    def resolve_registration(self, name: str) -> _FakeToolRegistration | None:
        return self._registrations.get(name)


class _FakeRuntime:
    """Fake runtime: only tool_registry is needed — hook entries are sourced
    from the module-level _RUNTIME_HOOK_POINTS constant, not the runtime."""

    def __init__(self, tools: list[tuple[_FakeToolManifest, bool]]) -> None:
        self.tool_registry = _FakeToolRegistry(tools)


# ---------------------------------------------------------------------------
# Hook entry tests — _hook_entries reads _RUNTIME_HOOK_POINTS, not a registry
# ---------------------------------------------------------------------------

def test_hook_entries_are_sourced_from_runtime_hook_points() -> None:
    """_hook_entries produces one entry per _RUNTIME_HOOK_POINTS point."""
    runtime = _FakeRuntime(tools=[])
    hooks = build_catalog(runtime)["verification"]["hooks"]
    hook_names = {h["name"] for h in hooks}
    assert hook_names == set(_RUNTIME_HOOK_POINTS)


def test_hook_entry_point_values_are_plain_camel_strings() -> None:
    """point values must be plain camelCase strings — never 'HookPoint.X'."""
    runtime = _FakeRuntime(tools=[])
    hooks = build_catalog(runtime)["verification"]["hooks"]
    for h in hooks:
        point = h["point"]
        assert point is not None, "point should not be None for runtime hook points"
        assert "HookPoint" not in point, (
            f"point '{point}' contains 'HookPoint' — use .value, not str(enum)"
        )
        # Must be a lowercase-starting camelCase identifier
        assert point[0].islower(), f"point '{point}' should start with lowercase"


def test_runtime_hook_points_are_always_on_security() -> None:
    """Built-in runtime hook points are alwaysOn=True, category='security'."""
    runtime = _FakeRuntime(tools=[])
    hooks = build_catalog(runtime)["verification"]["hooks"]
    assert len(hooks) == len(_RUNTIME_HOOK_POINTS)
    for h in hooks:
        assert h["alwaysOn"] is True
        assert h["category"] == "security"
        assert h["enabled"] is True


def test_hook_point_enum_value_is_camel_case() -> None:
    """Confirm HookPoint.value gives camelCase, not 'HookPoint.NAME'.

    BUG 2 root cause: even though HookPoint is a str-subclass enum, str()
    returns 'HookPoint.BEFORE_TOOL_USE' (the enum repr), not the value.
    The correct serialization is .value (or direct string comparison via ==).
    """
    # Spot-check a few known enum members
    assert HookPoint.BEFORE_TOOL_USE.value == "beforeToolUse"
    assert HookPoint.AFTER_TURN_END.value == "afterTurnEnd"
    assert HookPoint.BEFORE_LLM_CALL.value == "beforeLLMCall"
    # BUG: str() of HookPoint enum does NOT give the value — it gives "HookPoint.X"
    # This is the exact bug that was in the old _hook_entries implementation.
    assert str(HookPoint.BEFORE_TOOL_USE) == "HookPoint.BEFORE_TOOL_USE"
    # The fix: always use .value to get the plain camelCase string
    assert HookPoint.BEFORE_TOOL_USE.value == "beforeToolUse"
    assert "HookPoint" not in HookPoint.BEFORE_TOOL_USE.value


# ---------------------------------------------------------------------------
# Catalog structure tests
# ---------------------------------------------------------------------------

def test_catalog_has_curated_recipes_and_presets() -> None:
    runtime = _FakeRuntime(tools=[])
    catalog = build_catalog(runtime)
    assert len(catalog["verification"]["recipes"]) == len(RECIPES)
    assert len(catalog["verification"]["harnessPresets"]) == len(HARNESS_PRESETS)
    assert catalog["verification"]["recipes"][0]["id"]


def test_presets_sourced_from_real_catalog_with_hyphen_ids() -> None:
    runtime = _FakeRuntime(tools=[])
    presets = build_catalog(runtime)["verification"]["harnessPresets"]
    ids = {p["id"] for p in presets}
    # Real harness catalog uses hyphenated ids (matches hosted)
    assert "coding-verification" in ids
    assert "fact-grounding" in ids
    assert not any("_" in i for i in ids), "preset ids must be hyphenated"
    # Real catalog size — 9 of these are intended-dormant (pinned in
    # ``_INTENDED_DORMANT_PRESETS``) but kept in the catalog for parity / honest
    # surfacing. Floor is ≥30 to leave headroom for additions without coupling
    # this test to the exact count.
    assert len(presets) >= 30


def test_preset_entries_carry_enforcement_and_modes() -> None:
    runtime = _FakeRuntime(tools=[])
    presets = {p["id"]: p for p in build_catalog(runtime)["verification"]["harnessPresets"]}
    cv = presets["coding-verification"]
    assert cv["enforcement"] == "enforcing"
    assert cv["supportedModes"] == ["deterministic"]
    assert "defaultEnabled" in cv
    # security presets are always-on (enforced via PermissionGate, not this tab)
    assert presets["dangerous-patterns"]["enforcement"] == "always-on"
    # the answer-quality LLM seam is wired → enforcing
    assert presets["answer-quality"]["enforcement"] == "enforcing"
    # the self-claim LLM seam (C-MERGE-2) is wired → enforcing
    assert presets["self-claim"]["enforcement"] == "enforcing"
    # the claim-citation LLM seam (C4) is wired → enforcing
    assert presets["claim-citation"]["enforcement"] == "enforcing"
    # a still-unwired preset is honestly preview
    assert presets["coding-context"]["enforcement"] == "preview"


def test_preset_entries_carry_when_group_and_badges() -> None:
    runtime = _FakeRuntime(tools=[])
    presets = {p["id"]: p for p in build_catalog(runtime)["verification"]["harnessPresets"]}
    cv = presets["coding-verification"]
    # WHEN-group domain + raw fire-at points
    assert cv["domain"] == "coding"
    assert isinstance(cv["hookPoints"], list)
    # badge data: tier + opt-method + description
    assert cv["tier"] == "deterministic"
    assert cv["optMethod"] == "opt-out"
    assert cv["description"] and "parity" not in cv["description"].lower()
    # opt-in wired preset
    assert presets["fact-grounding"]["optMethod"] == "opt-in"
    assert presets["fact-grounding"]["domain"] == "research"
    # security → always-on tier + always-on domain
    sec = presets["dangerous-patterns"]
    assert sec["tier"] == "always-on"
    assert sec["domain"] == "always-on"
    # the answer-quality LLM seam badges the llm tier + opt-in (honest, not "det")
    aq = presets["answer-quality"]
    assert aq["tier"] == "llm"
    assert aq["optMethod"] == "opt-in"
    assert aq["domain"] == "delivery"
    assert aq["description"]
    # a still-unwired preview preset → no tier / no opt-method
    cc = presets["coding-context"]
    assert cc["tier"] is None
    assert cc["optMethod"] is None


# ---------------------------------------------------------------------------
# Tool tests
# ---------------------------------------------------------------------------

def test_tools_reflect_registry_enabled_state() -> None:
    """enabled comes from resolve_registration, not from the manifest itself."""
    web_manifest = _FakeToolManifest(
        "web_fetch",
        description="web_fetch desc",
        source_kind="builtin",
    )
    shell_manifest = _FakeToolManifest(
        "shell",
        description="shell desc",
        source_kind="builtin",
    )
    runtime = _FakeRuntime(
        tools=[
            (web_manifest, True),    # enabled=True via registration
            (shell_manifest, False), # enabled=False via registration
        ],
    )
    tools = build_catalog(runtime)["tools"]
    names = {t["name"]: t for t in tools}

    assert names["web_fetch"]["enabled"] is True
    assert names["shell"]["enabled"] is False
    assert names["web_fetch"]["description"] == "web_fetch desc"


def test_tools_shape_has_required_keys() -> None:
    """Every tool entry must expose name, description, enabled, source, dangerous."""
    manifest = _FakeToolManifest(
        "my_tool", description="does stuff", source_kind="custom-plugin", dangerous=True
    )
    runtime = _FakeRuntime(tools=[(manifest, True)])
    tools = build_catalog(runtime)["tools"]
    assert len(tools) == 1
    t = tools[0]
    assert set(t.keys()) >= {"name", "description", "enabled", "source", "dangerous"}
    assert t["source"] == "custom-plugin"
    assert t["dangerous"] is True
    assert t["name"] == "my_tool"


# ---------------------------------------------------------------------------
# Real-runtime integration test
# Gate that would have caught both BUG 1 (AttributeError on hook_registry)
# and BUG 2 (HookPoint serialization producing "HookPoint.X" strings).
# ---------------------------------------------------------------------------

def test_build_catalog_real_runtime_no_attr_error_and_no_hookpoint_leak(
    tmp_path, monkeypatch
) -> None:
    """build_catalog(real_runtime) must:
    1. Not raise AttributeError (no hook_registry on OpenMagiRuntime).
    2. Produce JSON-serializable output (json.dumps must not raise).
    3. Contain no 'HookPoint.' substring in any hook entry's point field.
    """
    from magi_agent.config.models import BuildInfo, RuntimeConfig
    from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

    # Isolate config from developer's ~/.magi/config.toml
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))

    runtime = OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="test-bot",
            user_id="test-user",
            gateway_token="test-token",
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )

    # Must not raise AttributeError
    result = build_catalog(runtime)

    # Must be JSON-serializable
    serialized = json.dumps(result)

    # No HookPoint enum leakage
    assert "HookPoint." not in serialized, (
        "Serialized catalog contains 'HookPoint.' — enum was not converted to .value"
    )

    # Each hook entry's point must be None or a plain camelCase string
    for hook in result["verification"]["hooks"]:
        point = hook.get("point")
        if point is not None:
            assert "HookPoint" not in point, (
                f"Hook entry point '{point}' leaks enum repr"
            )
            assert point[0].islower(), (
                f"Hook entry point '{point}' should start with lowercase camelCase"
            )
