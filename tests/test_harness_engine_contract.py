import pytest
from pydantic import ValidationError

from magi_agent.harness.engine import HarnessEngine, HarnessResolutionRequest
from magi_agent.hooks.manifest import HookManifest, HookPoint
from magi_agent.hooks.scope import HookScope, HookScopeContext
from magi_agent.tools.manifest import ToolSource


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


def test_harness_engine_resolves_role_scoped_snapshot_before_hook_execution() -> None:
    engine = HarnessEngine(
        hooks=(
            hook("codingVerification", scope=HookScope(agent_roles=("coding",))),
            hook("sourceAuthority", scope=HookScope(agent_roles=("research",))),
            hook("pathSafety", scope=HookScope(hard_safety=True)),
        )
    )

    selected, state = engine.resolve(
        HarnessResolutionRequest(agent_role="research", spawn_depth=1),
    )

    assert [item.name for item in selected] == ["sourceAuthority", "pathSafety"]
    assert state.agent_role == "research"
    assert state.run_on == "child"
    assert state.effective_hooks == ("sourceAuthority", "pathSafety")
    assert state.skipped_by_scope == ("codingVerification",)
    assert state.effective_harness_packs == ("research", "hard-safety")
    assert isinstance(state.hook_scope_context(), HookScopeContext)


def test_harness_engine_filters_native_and_custom_plugin_hooks_by_scope() -> None:
    engine = HarnessEngine(
        hooks=(
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
    )

    selected, state = engine.resolve(
        HarnessResolutionRequest(agent_role="coding", spawn_depth=1),
    )

    assert [item.name for item in selected] == ["customCodingHook"]
    assert state.effective_hooks == ("customCodingHook",)
    assert state.skipped_by_scope == ("nativeResearchHook",)


@pytest.mark.parametrize(
    ("request_kwargs", "message"),
    (
        (
            {"agent_role": "coding", "run_on": "child", "spawn_depth": 0},
            "child runs must use spawnDepth greater than 0",
        ),
        (
            {"agent_role": "coding", "run_on": "main", "spawn_depth": 1},
            "main runs must use spawnDepth=0",
        ),
    ),
)
def test_harness_resolution_request_rejects_inconsistent_run_on_spawn_depth(
    request_kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        HarnessResolutionRequest(**request_kwargs)


@pytest.mark.parametrize("agent_role", ("general", "coding", "research"))
def test_harness_resolution_request_accepts_supported_agent_roles(agent_role: str) -> None:
    request = HarnessResolutionRequest(agentRole=agent_role)

    assert request.agent_role == agent_role


@pytest.mark.parametrize(
    ("base_kwargs", "update", "message"),
    (
        (
            {"agent_role": "coding", "run_on": "main", "spawn_depth": 0},
            {"spawn_depth": 1},
            "main runs must use spawnDepth=0",
        ),
        (
            {"agent_role": "coding", "run_on": "child", "spawn_depth": 1},
            {"spawnDepth": 0},
            "child runs must use spawnDepth greater than 0",
        ),
        (
            {"agent_role": "coding", "spawn_depth": 0},
            {"runOn": "child"},
            "child runs must use spawnDepth greater than 0",
        ),
    ),
)
def test_harness_resolution_request_model_copy_revalidates_run_on_spawn_depth(
    base_kwargs: dict[str, object],
    update: dict[str, object],
    message: str,
) -> None:
    request = HarnessResolutionRequest(**base_kwargs)

    with pytest.raises(ValidationError, match=message):
        request.model_copy(update=update)


def test_resolved_harness_pack_components_are_immutable_inside_frozen_state() -> None:
    _, state = HarnessEngine().resolve(HarnessResolutionRequest(agent_role="coding"))

    with pytest.raises(TypeError):
        state.coding.components["tools"] = ("MutatedTool",)

    dumped = state.model_dump(by_alias=True)

    assert dumped["coding"]["components"]["tools"] == (
        "FileRead",
        "FileEdit",
        "PatchApply",
    )
