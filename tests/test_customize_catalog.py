"""Tests for magi_agent.customize.catalog.

Fakes MIRROR the real registry shapes:
- hook_registry.list_all() → list[HookManifest-shaped objects]
  Fields: name, point, enabled (baked in), security_critical, scope.hard_safety, opt_out
- tool_registry.list_all() → list[ToolManifest-shaped objects]
  Fields: name, description, source (object with .kind), dangerous, enabled_by_default
  live enabled comes from tool_registry.resolve_registration(name).enabled
"""
from __future__ import annotations

from magi_agent.customize.catalog import (
    HARNESS_PRESETS,
    RECIPES,
    build_catalog,
)


# ---------------------------------------------------------------------------
# Minimal fake scope mirroring HookScope (just the field catalog.py reads)
# ---------------------------------------------------------------------------
class _FakeScope:
    def __init__(self, hard_safety: bool = False) -> None:
        self.hard_safety = hard_safety


# ---------------------------------------------------------------------------
# HookManifest-shaped fake — matches what hook_registry.list_all() returns.
# Fields used by _hook_entries: name, point, enabled, security_critical,
# scope (with .hard_safety), opt_out.
# ---------------------------------------------------------------------------
class _FakeHookManifest:
    def __init__(
        self,
        name: str,
        point: object,
        enabled: bool = True,
        security_critical: bool = False,
        hard_safety: bool = False,
        opt_out: bool = True,
    ) -> None:
        self.name = name
        self.point = point
        self.enabled = enabled
        self.security_critical = security_critical
        self.scope = _FakeScope(hard_safety=hard_safety)
        self.opt_out = opt_out


# ---------------------------------------------------------------------------
# ToolManifest-shaped fake — matches what tool_registry.list_all() returns.
# The manifest itself does NOT carry live enabled; resolve_registration does.
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


# ---------------------------------------------------------------------------
# Fake registries — hook_registry.list_all() returns manifests directly;
# tool_registry.list_all() returns manifests directly AND exposes
# resolve_registration(name) returning a registration with .enabled.
# ---------------------------------------------------------------------------
class _FakeHookRegistry:
    def __init__(self, manifests: list[_FakeHookManifest]) -> None:
        self._manifests = manifests

    def list_all(self) -> list[_FakeHookManifest]:
        return list(self._manifests)


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
    def __init__(
        self,
        hooks: list[_FakeHookManifest],
        tools: list[tuple[_FakeToolManifest, bool]],
    ) -> None:
        self.hook_registry = _FakeHookRegistry(hooks)
        self.tool_registry = _FakeToolRegistry(tools)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_catalog_has_curated_recipes_and_presets() -> None:
    runtime = _FakeRuntime(hooks=[], tools=[])
    catalog = build_catalog(runtime)
    assert len(catalog["verification"]["recipes"]) == len(RECIPES)
    assert len(catalog["verification"]["harnessPresets"]) == len(HARNESS_PRESETS)
    assert catalog["verification"]["recipes"][0]["id"]


def test_security_critical_hook_is_always_on_security() -> None:
    """security_critical=True → alwaysOn True, category 'security'."""
    runtime = _FakeRuntime(
        hooks=[
            _FakeHookManifest(
                "secret-scan",
                "beforeToolUse",
                enabled=True,
                security_critical=True,
            ),
            _FakeHookManifest(
                "nudge",
                "afterTurnEnd",
                enabled=False,
                security_critical=False,
            ),
        ],
        tools=[],
    )
    hooks = build_catalog(runtime)["verification"]["hooks"]
    secret = next(h for h in hooks if h["name"] == "secret-scan")
    nudge = next(h for h in hooks if h["name"] == "nudge")

    assert secret["alwaysOn"] is True
    assert secret["category"] == "security"
    assert secret["enabled"] is True

    assert nudge["alwaysOn"] is False
    assert nudge["category"] == "general"
    assert nudge["enabled"] is False


def test_hard_safety_scope_hook_is_always_on_security() -> None:
    """scope.hard_safety=True → alwaysOn True, category 'security'."""
    runtime = _FakeRuntime(
        hooks=[
            _FakeHookManifest("hard-guard", "beforeLLMCall", hard_safety=True),
        ],
        tools=[],
    )
    hooks = build_catalog(runtime)["verification"]["hooks"]
    guard = next(h for h in hooks if h["name"] == "hard-guard")
    assert guard["alwaysOn"] is True
    assert guard["category"] == "security"


def test_non_opt_out_hook_is_always_on() -> None:
    """opt_out=False → alwaysOn True (mirrors is_protected_manifest logic)."""
    runtime = _FakeRuntime(
        hooks=[
            _FakeHookManifest("mandatory", "beforeCommit", opt_out=False),
        ],
        tools=[],
    )
    hooks = build_catalog(runtime)["verification"]["hooks"]
    m = next(h for h in hooks if h["name"] == "mandatory")
    assert m["alwaysOn"] is True
    assert m["category"] == "security"


def test_plain_hook_is_general_and_reflects_enabled() -> None:
    """A plain hook (no security flags) → alwaysOn False, enabled reflects manifest."""
    runtime = _FakeRuntime(
        hooks=[
            _FakeHookManifest("plain-disabled", "afterToolUse", enabled=False),
        ],
        tools=[],
    )
    hooks = build_catalog(runtime)["verification"]["hooks"]
    h = hooks[0]
    assert h["alwaysOn"] is False
    assert h["category"] == "general"
    assert h["enabled"] is False


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
        hooks=[],
        tools=[
            (web_manifest, True),   # enabled=True via registration
            (shell_manifest, False),  # enabled=False via registration
        ],
    )
    tools = build_catalog(runtime)["tools"]
    names = {t["name"]: t for t in tools}

    assert names["web_fetch"]["enabled"] is True
    assert names["shell"]["enabled"] is False
    assert names["web_fetch"]["description"] == "web_fetch desc"


def test_tools_shape_has_required_keys() -> None:
    """Every tool entry must expose name, description, enabled, source, dangerous."""
    manifest = _FakeToolManifest("my_tool", description="does stuff", source_kind="custom-plugin", dangerous=True)
    runtime = _FakeRuntime(hooks=[], tools=[(manifest, True)])
    tools = build_catalog(runtime)["tools"]
    assert len(tools) == 1
    t = tools[0]
    assert set(t.keys()) >= {"name", "description", "enabled", "source", "dangerous"}
    assert t["source"] == "custom-plugin"
    assert t["dangerous"] is True
    assert t["name"] == "my_tool"
