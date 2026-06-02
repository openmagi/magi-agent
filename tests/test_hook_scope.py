from openmagi_core_agent.harness.resolved import (
    build_default_resolved_harness_state,
    filter_hooks_for_harness,
    resolve_scoped_harness_hooks,
)
from openmagi_core_agent.hooks.manifest import HookManifest, HookPoint
from openmagi_core_agent.hooks.scope import HookScope
from openmagi_core_agent.tools.manifest import ToolSource


def hook(
    name: str,
    *,
    scope: HookScope,
    source_kind: str = "builtin",
    security_critical: bool = False,
) -> HookManifest:
    return HookManifest(
        name=name,
        point=HookPoint.BEFORE_TOOL_USE,
        description=f"{name} hook",
        source=ToolSource(kind=source_kind, package="test"),
        scope=scope,
        security_critical=security_critical,
    )


def test_research_child_excludes_coding_only_gates_and_records_scope_skip() -> None:
    state = build_default_resolved_harness_state(agent_role="research", spawn_depth=1)
    hooks = (
        hook("codingVerificationGate", scope=HookScope(agent_roles=("coding",))),
        hook("sourceAuthority", scope=HookScope(agent_roles=("research",))),
        hook("sealedFiles", scope=HookScope(hard_safety=True)),
    )

    selected, resolved = resolve_scoped_harness_hooks(hooks, state)

    assert [item.name for item in selected] == ["sourceAuthority", "sealedFiles"]
    assert resolved.run_on == "child"
    assert resolved.agent_role == "research"
    assert resolved.spawn_depth == 1
    assert resolved.effective_hooks == ("sourceAuthority", "sealedFiles")
    assert resolved.skipped_by_scope == ("codingVerificationGate",)
    assert resolved.effective_harness_packs == ("research", "hard-safety")


def test_coding_child_excludes_research_only_gates_by_default() -> None:
    state = build_default_resolved_harness_state(agent_role="coding", spawn_depth=1)
    hooks = (
        hook("codingVerificationGate", scope=HookScope(agent_roles=("coding",))),
        hook("claimCitationGate", scope=HookScope(agent_roles=("research",))),
        hook("dangerousPatterns", scope=HookScope(hard_safety=True)),
    )

    selected = filter_hooks_for_harness(hooks, state)

    assert [item.name for item in selected] == ["codingVerificationGate", "dangerousPatterns"]


def test_hard_safety_hooks_apply_to_main_and_all_child_depths() -> None:
    hard = hook(
        "secretSafety",
        scope=HookScope(run_on=("main",), agent_roles=("general",), max_spawn_depth=0, hard_safety=True),
    )

    assert filter_hooks_for_harness((hard,), build_default_resolved_harness_state(spawn_depth=0))
    assert filter_hooks_for_harness(
        (hard,),
        build_default_resolved_harness_state(agent_role="research", spawn_depth=5),
    )


def test_security_critical_hook_applies_even_without_scope_hard_safety() -> None:
    restrictive_scope = HookScope(
        run_on=("main",),
        agent_roles=("general",),
        max_spawn_depth=0,
        hard_safety=False,
    )
    hooks = (
        hook("sealedFiles", scope=restrictive_scope, security_critical=True),
        hook("codingStyle", scope=restrictive_scope),
    )

    selected, resolved = resolve_scoped_harness_hooks(
        hooks,
        build_default_resolved_harness_state(agent_role="research", spawn_depth=5),
    )

    assert [item.name for item in selected] == ["sealedFiles"]
    assert resolved.effective_hooks == ("sealedFiles",)
    assert resolved.skipped_by_scope == ("codingStyle",)


def test_spawn_depth_filtering_applies_before_non_hard_safety_execution() -> None:
    scoped = hook("childDepthLimited", scope=HookScope(run_on=("child",), min_spawn_depth=1, max_spawn_depth=2))

    assert filter_hooks_for_harness((scoped,), build_default_resolved_harness_state(spawn_depth=0)) == ()
    assert filter_hooks_for_harness((scoped,), build_default_resolved_harness_state(spawn_depth=1))
    assert filter_hooks_for_harness((scoped,), build_default_resolved_harness_state(spawn_depth=3)) == ()


def test_native_and_custom_plugin_hooks_declare_scope_and_are_filtered_before_execution() -> None:
    hooks = (
        hook(
            "nativeResearchHook",
            scope=HookScope(run_on=("child",), agent_roles=("research",)),
            source_kind="native-plugin",
        ),
        hook(
            "customCodingHook",
            scope=HookScope(run_on=("child",), agent_roles=("coding",)),
            source_kind="custom-plugin",
        ),
    )

    selected, resolved = resolve_scoped_harness_hooks(
        hooks,
        build_default_resolved_harness_state(agent_role="coding", spawn_depth=1),
    )

    assert [item.name for item in selected] == ["customCodingHook"]
    assert resolved.effective_hooks == ("customCodingHook",)
    assert resolved.skipped_by_scope == ("nativeResearchHook",)
