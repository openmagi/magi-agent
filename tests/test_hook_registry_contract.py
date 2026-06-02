import pytest
from pydantic import ValidationError

from openmagi_core_agent.harness.resolved import (
    build_default_resolved_harness_state,
    filter_hooks_for_harness,
)
from openmagi_core_agent.hooks.manifest import HookManifest, HookPoint
from openmagi_core_agent.hooks.registry import HookRegistry
from openmagi_core_agent.hooks.scope import HookScope
from openmagi_core_agent.tools.manifest import ToolSource


def hook(
    name: str,
    *,
    point: HookPoint = HookPoint.BEFORE_TOOL_USE,
    priority: int = 100,
    source_kind: str = "custom-plugin",
    security_critical: bool = False,
    hard_safety: bool = False,
    scope: HookScope | None = None,
    opt_out: bool = True,
    blocking: bool = True,
    fail_open: bool = False,
    timeout_ms: int = 5_000,
    enabled: bool = True,
) -> HookManifest:
    return HookManifest(
        name=name,
        point=point,
        description=f"{name} hook",
        source=ToolSource(kind=source_kind, package="tests.hooks"),
        priority=priority,
        scope=scope or HookScope(hard_safety=hard_safety),
        security_critical=security_critical,
        opt_out=opt_out,
        blocking=blocking,
        fail_open=fail_open,
        timeout_ms=timeout_ms,
        enabled=enabled,
    )


def force_set(model: object, field: str, value: object) -> None:
    object.__setattr__(model, field, value)


def test_register_list_and_resolve_returns_deterministic_hook_metadata_by_point() -> None:
    registry = HookRegistry()
    registry.register(hook("later", priority=20))
    registry.register(hook("alpha", priority=10))
    registry.register(hook("beta", priority=10))
    registry.register(hook("otherPoint", point=HookPoint.AFTER_TOOL_USE, priority=0))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(hook("alpha", priority=0))

    assert [item.name for item in registry.list_all()] == [
        "alpha",
        "beta",
        "later",
        "otherPoint",
    ]
    assert [item.name for item in registry.list_enabled(point=HookPoint.BEFORE_TOOL_USE)] == [
        "alpha",
        "beta",
        "later",
    ]
    assert registry.resolve("alpha") == hook("alpha", priority=10)
    assert registry.resolve("missing") is None


def test_public_reads_and_register_replace_ownership_are_defensive_for_nested_scope() -> None:
    registry = HookRegistry()
    caller_manifest = hook("callerOwned", priority=30)

    registry.register(caller_manifest)
    force_set(caller_manifest.scope, "hard_safety", True)
    force_set(caller_manifest, "priority", 0)

    stored = registry.resolve("callerOwned")
    assert stored is not None
    assert stored.priority == 30
    assert stored.scope.hard_safety is False

    returned = registry.list_all()[0]
    assert returned.scope is not stored.scope
    force_set(returned.scope, "hard_safety", True)
    force_set(returned, "priority", 1)

    fresh = registry.resolve("callerOwned")
    assert fresh is not None
    assert fresh.priority == 30
    assert fresh.scope.hard_safety is False

    replacement = hook("callerOwned", priority=40)
    registry.replace(replacement)
    force_set(replacement.scope, "hard_safety", True)
    force_set(replacement, "priority", 2)

    replaced = registry.resolve("callerOwned")
    assert replaced is not None
    assert replaced.priority == 40
    assert replaced.scope.hard_safety is False


def test_disable_excludes_opt_out_hook_from_enabled_list_and_enable_restores_it() -> None:
    registry = HookRegistry()
    registry.register(hook("dashboardOptional", source_kind="builtin", priority=10, opt_out=True))
    registry.register(hook("alwaysOn", priority=20))

    registry.disable("dashboardOptional")

    assert [item.name for item in registry.list_all()] == ["alwaysOn", "dashboardOptional"]
    assert [item.name for item in registry.list_enabled(point=HookPoint.BEFORE_TOOL_USE)] == [
        "alwaysOn"
    ]

    registry.enable("dashboardOptional")

    assert [item.name for item in registry.list_enabled(point=HookPoint.BEFORE_TOOL_USE)] == [
        "dashboardOptional",
        "alwaysOn",
    ]


@pytest.mark.parametrize(
    "manifest",
    (
        hook("securityCritical", security_critical=True, opt_out=True),
        hook("hardSafety", hard_safety=True, opt_out=True),
        hook("required", opt_out=False),
    ),
)
def test_security_hard_safety_and_required_hooks_cannot_be_disabled_or_unregistered(
    manifest: HookManifest,
) -> None:
    registry = HookRegistry()
    registry.register(manifest)

    with pytest.raises(ValueError, match="cannot disable"):
        registry.disable(manifest.name)

    with pytest.raises(ValueError, match="cannot unregister"):
        registry.unregister(manifest.name)

    assert registry.resolve(manifest.name) is not None
    assert [item.name for item in registry.list_enabled(point=manifest.point)] == [manifest.name]


@pytest.mark.parametrize("source_kind", ("custom-plugin", "runtime"))
def test_custom_plugin_and_runtime_hooks_can_unregister(source_kind: str) -> None:
    registry = HookRegistry()
    registry.register(hook(f"{source_kind}Hook", source_kind=source_kind))

    removed = registry.unregister(f"{source_kind}Hook")

    assert removed.name == f"{source_kind}Hook"
    assert registry.resolve(f"{source_kind}Hook") is None


@pytest.mark.parametrize("source_kind", ("builtin", "native-plugin"))
def test_builtin_and_native_plugin_hooks_cannot_unregister(source_kind: str) -> None:
    registry = HookRegistry()
    registry.register(hook(f"{source_kind}Hook", source_kind=source_kind, opt_out=True))

    with pytest.raises(ValueError, match="cannot unregister"):
        registry.unregister(f"{source_kind}Hook")

    assert registry.resolve(f"{source_kind}Hook") is not None


def test_replace_preserves_protected_status_after_downgrade() -> None:
    registry = HookRegistry()
    registry.register(hook("sealedFiles", source_kind="builtin", security_critical=True, opt_out=False))

    registry.replace(hook("sealedFiles", source_kind="custom-plugin", security_critical=False, opt_out=True))

    with pytest.raises(ValueError, match="cannot disable"):
        registry.disable("sealedFiles")
    with pytest.raises(ValueError, match="cannot unregister"):
        registry.unregister("sealedFiles")

    downgraded = registry.resolve("sealedFiles")
    assert downgraded is not None
    assert downgraded.source.kind == "custom-plugin"
    assert downgraded.security_critical is True
    assert downgraded.opt_out is False


def test_replace_preserves_security_critical_selection_metadata_after_downgrade() -> None:
    registry = HookRegistry()
    registry.register(
        hook(
            "permissionGate",
            point=HookPoint.BEFORE_TOOL_USE,
            scope=HookScope(run_on=("main",), agent_roles=("general",), max_spawn_depth=0),
            security_critical=True,
            opt_out=True,
        )
    )

    registry.replace(
        hook(
            "permissionGate",
            point=HookPoint.AFTER_COMMIT,
            scope=HookScope(run_on=("main",), agent_roles=("general",), max_spawn_depth=0),
            security_critical=False,
            opt_out=True,
        )
    )

    before_tool_use = registry.list_enabled(point=HookPoint.BEFORE_TOOL_USE)
    assert [item.name for item in before_tool_use] == ["permissionGate"]
    assert registry.list_enabled(point=HookPoint.AFTER_COMMIT) == []

    resolved = registry.resolve("permissionGate")
    assert resolved is not None
    assert resolved.point is HookPoint.BEFORE_TOOL_USE
    assert resolved.security_critical is True
    assert filter_hooks_for_harness(
        (resolved,),
        build_default_resolved_harness_state(agent_role="research", spawn_depth=5),
    ) == (resolved,)


def test_replace_preserves_hard_safety_scope_after_downgrade() -> None:
    registry = HookRegistry()
    registry.register(
        hook(
            "secretSafety",
            scope=HookScope(run_on=("main",), agent_roles=("general",), max_spawn_depth=0, hard_safety=True),
            opt_out=True,
        )
    )

    registry.replace(
        hook(
            "secretSafety",
            scope=HookScope(run_on=("main",), agent_roles=("general",), max_spawn_depth=0, hard_safety=False),
            opt_out=True,
        )
    )

    resolved = registry.resolve("secretSafety")
    assert resolved is not None
    assert resolved.scope.hard_safety is True
    assert filter_hooks_for_harness(
        (resolved,),
        build_default_resolved_harness_state(agent_role="research", spawn_depth=5),
    ) == (resolved,)


def test_replace_preserves_required_opt_out_metadata_after_downgrade() -> None:
    registry = HookRegistry()
    registry.register(hook("sealedFiles", opt_out=False))

    registry.replace(hook("sealedFiles", opt_out=True))

    with pytest.raises(ValueError, match="cannot disable"):
        registry.disable("sealedFiles")

    resolved = registry.resolve("sealedFiles")
    assert resolved is not None
    assert resolved.opt_out is False


def test_replace_preserves_protected_fail_closed_blocking_metadata_after_downgrade() -> None:
    registry = HookRegistry()
    registry.register(
        hook(
            "permissionGate",
            security_critical=True,
            blocking=True,
            fail_open=False,
        )
    )

    registry.replace(
        hook(
            "permissionGate",
            security_critical=True,
            blocking=False,
            fail_open=True,
        )
    )

    resolved = registry.resolve("permissionGate")
    assert resolved is not None
    assert resolved.blocking is True
    assert resolved.fail_open is False


def test_replace_preserves_protected_timeout_after_downgrade() -> None:
    registry = HookRegistry()
    registry.register(hook("sealedFiles", opt_out=False, timeout_ms=1_000))

    registry.replace(hook("sealedFiles", opt_out=False, timeout_ms=30_000))

    resolved = registry.resolve("sealedFiles")
    assert resolved is not None
    assert resolved.timeout_ms == 1_000


def test_replace_preserves_protected_priority_ahead_of_custom_hooks() -> None:
    registry = HookRegistry()
    registry.register(
        hook(
            "permissionGate",
            priority=0,
            security_critical=True,
            blocking=True,
            fail_open=False,
        )
    )
    registry.register(hook("customObserver", priority=50))

    registry.replace(
        hook(
            "permissionGate",
            priority=100,
            security_critical=True,
            blocking=True,
            fail_open=False,
        )
    )

    enabled = registry.list_enabled(point=HookPoint.BEFORE_TOOL_USE)
    assert [item.name for item in enabled] == ["permissionGate", "customObserver"]
    resolved = registry.resolve("permissionGate")
    assert resolved is not None
    assert resolved.priority == 0
    assert resolved.blocking is True
    assert resolved.fail_open is False


def test_replace_allows_non_protected_hooks_to_change_runtime_policy_metadata() -> None:
    registry = HookRegistry()
    registry.register(hook("observer", blocking=True, fail_open=False, timeout_ms=1_000))

    registry.replace(hook("observer", blocking=False, fail_open=True, timeout_ms=30_000))

    resolved = registry.resolve("observer")
    assert resolved is not None
    assert resolved.blocking is False
    assert resolved.fail_open is True
    assert resolved.timeout_ms == 30_000


def test_replace_preserves_builtin_unregister_protection_after_source_downgrade() -> None:
    registry = HookRegistry()
    registry.register(hook("builtinOptional", source_kind="builtin", opt_out=True))

    registry.disable("builtinOptional")
    registry.enable("builtinOptional")
    registry.replace(hook("builtinOptional", source_kind="custom-plugin", opt_out=True))

    with pytest.raises(ValueError, match="cannot unregister"):
        registry.unregister("builtinOptional")

    assert registry.resolve("builtinOptional") is not None


def test_register_and_replace_keep_disable_protected_hooks_enabled() -> None:
    registry = HookRegistry()
    registry.register(hook("criticalDisabled", security_critical=True, enabled=False))

    critical = registry.resolve("criticalDisabled")
    assert critical is not None
    assert critical.enabled is True
    assert [item.name for item in registry.list_enabled(point=HookPoint.BEFORE_TOOL_USE)] == [
        "criticalDisabled"
    ]

    registry.register(hook("optional", enabled=True))
    registry.disable("optional")
    registry.replace(hook("optional", security_critical=True, enabled=False))

    upgraded = registry.resolve("optional")
    assert upgraded is not None
    assert upgraded.enabled is True
    assert [item.name for item in registry.list_enabled(point=HookPoint.BEFORE_TOOL_USE)] == [
        "criticalDisabled",
        "optional",
    ]


def test_stats_returns_zeroed_deterministic_fields_by_hook_name() -> None:
    registry = HookRegistry()
    registry.register(hook("beta", priority=20))
    registry.register(hook("alpha", priority=10))

    assert registry.stats() == {
        "alpha": {
            "totalRuns": 0,
            "timeouts": 0,
            "errors": 0,
            "blocks": 0,
            "avgDurationMs": 0,
            "lastRunAt": 0,
        },
        "beta": {
            "totalRuns": 0,
            "timeouts": 0,
            "errors": 0,
            "blocks": 0,
            "avgDurationMs": 0,
            "lastRunAt": 0,
        },
    }


def test_hook_manifest_accepts_camel_case_opt_out_alias() -> None:
    manifest = HookManifest.model_validate(
        {
            "name": "permissionGate",
            "point": "beforeToolUse",
            "description": "permissionGate hook",
            "source": {"kind": "builtin", "package": "tests.hooks"},
            "optOut": False,
        }
    )

    assert manifest.opt_out is False


def test_hook_manifest_rejects_unexpected_extra_fields() -> None:
    with pytest.raises(ValidationError, match="extra"):
        HookManifest.model_validate(
            {
                "name": "permissionGate",
                "point": "beforeToolUse",
                "description": "permissionGate hook",
                "source": {"kind": "builtin", "package": "tests.hooks"},
                "surprise": True,
            }
        )
