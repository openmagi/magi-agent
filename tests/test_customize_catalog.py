from magi_agent.customize.catalog import (
    HARNESS_PRESETS,
    RECIPES,
    build_catalog,
)


class _FakeManifest:
    def __init__(self, name, point, description="", source="builtin", dangerous=False):
        self.name = name
        self.point = point
        self.description = description
        self.source = source
        self.dangerous = dangerous


class _FakeHookReg:
    def __init__(self, name, point, enabled, protected):
        self.manifest = _FakeManifest(name, point)
        self.enabled = enabled
        self.protected = protected


class _FakeToolReg:
    def __init__(self, name, enabled, protected=False):
        self.manifest = _FakeManifest(name, point=None, description=f"{name} desc")
        self.enabled = enabled
        self.protected = protected


class _FakeRegistry:
    def __init__(self, items):
        self._items = items

    def list_all(self):
        return list(self._items)


class _FakeRuntime:
    def __init__(self, hooks, tools):
        self.hook_registry = _FakeRegistry(hooks)
        self.tool_registry = _FakeRegistry(tools)


def test_catalog_has_curated_recipes_and_presets() -> None:
    runtime = _FakeRuntime(hooks=[], tools=[])
    catalog = build_catalog(runtime)
    assert len(catalog["verification"]["recipes"]) == len(RECIPES)
    assert len(catalog["verification"]["harnessPresets"]) == len(HARNESS_PRESETS)
    assert catalog["verification"]["recipes"][0]["id"]


def test_protected_hook_is_always_on_security() -> None:
    runtime = _FakeRuntime(
        hooks=[
            _FakeHookReg("secret-scan", "before_tool_use", enabled=True, protected=True),
            _FakeHookReg("nudge", "after_turn_end", enabled=False, protected=False),
        ],
        tools=[],
    )
    hooks = build_catalog(runtime)["verification"]["hooks"]
    secret = next(h for h in hooks if h["name"] == "secret-scan")
    nudge = next(h for h in hooks if h["name"] == "nudge")
    assert secret["alwaysOn"] is True and secret["category"] == "security"
    assert nudge["alwaysOn"] is False and nudge["category"] == "general"
    assert nudge["enabled"] is False


def test_tools_reflect_registry() -> None:
    runtime = _FakeRuntime(
        hooks=[],
        tools=[_FakeToolReg("web_fetch", enabled=True), _FakeToolReg("shell", enabled=False)],
    )
    tools = build_catalog(runtime)["tools"]
    names = {t["name"]: t for t in tools}
    assert names["web_fetch"]["enabled"] is True
    assert names["shell"]["enabled"] is False
    assert names["web_fetch"]["description"] == "web_fetch desc"
