import pytest
from pydantic import ValidationError

from magi_agent.harness.profiles import (
    DEFAULT_PROFILE_NAME,
    build_default_profile,
)
from magi_agent.hooks.manifest import HookManifest, HookPoint
from magi_agent.hooks.result import HookResult
from magi_agent.plugins.manifest import (
    PluginCapability,
    PluginKind,
    PluginManifest,
)
from magi_agent.tools.manifest import Budget, ToolManifest, ToolSource


def _base_tool_manifest_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "name": "FileRead",
        "description": "Read workspace files through OpenMagiRuntime.",
        "kind": "core",
        "source": ToolSource(kind="builtin", package="openmagi.core"),
        "permission": "read",
        "input_schema": {"type": "object"},
        "timeout_ms": 30_000,
    }
    kwargs.update(overrides)
    return kwargs


def test_openmagi_opinionated_profile_models_default_on_harness_packs() -> None:
    profile = build_default_profile()

    assert profile.name == DEFAULT_PROFILE_NAME == "openmagi-opinionated"
    pack_by_name = {pack.name: pack for pack in profile.harness_packs}
    assert pack_by_name["coding"].enabled_by_default is True
    assert pack_by_name["research"].enabled_by_default is True
    assert pack_by_name["verification"].enabled_by_default is True
    assert pack_by_name["coding"].opt_out is True
    assert pack_by_name["research"].opt_out is True
    assert pack_by_name["verification"].opt_out is True
    assert profile.hard_safety.enabled_by_default is True
    assert profile.hard_safety.opt_out is False


def test_tool_manifest_captures_permission_budget_source_and_plugin_identity() -> None:
    manifest = ToolManifest(
        name="FileRead",
        description="Read workspace files through OpenMagiRuntime.",
        kind="core",
        source=ToolSource(kind="builtin", package="openmagi.core"),
        permission="read",
        input_schema={"type": "object"},
        timeout_ms=30_000,
        budget=Budget(
            max_calls_per_turn=10,
            max_parallel=2,
            output_chars=16_000,
            transcript_chars=4_000,
        ),
        plugin_id=None,
        dangerous=False,
        is_concurrency_safe=True,
        mutates_workspace=False,
        available_in_modes=("plan", "act"),
        tags=("workspace", "read"),
        should_defer=False,
    )

    assert manifest.name == "FileRead"
    assert manifest.kind == "core"
    assert manifest.source.kind == "builtin"
    assert manifest.permission == "read"
    assert manifest.output_schema is None
    assert manifest.timeout_ms == 30_000
    assert manifest.budget.max_calls_per_turn == 10
    assert manifest.budget.output_chars == 16_000
    assert manifest.dangerous is False
    assert manifest.is_concurrency_safe is True
    assert manifest.mutates_workspace is False
    assert manifest.available_in_modes == ("plan", "act")
    assert manifest.tags == ("workspace", "read")
    assert manifest.should_defer is False
    assert manifest.enabled_by_default is False
    assert manifest.model_dump(by_alias=True)["isConcurrencySafe"] is True
    assert manifest.model_dump(by_alias=True)["outputSchema"] is None


def test_tool_manifest_rejects_permissions_outside_current_contract() -> None:
    with pytest.raises(ValidationError):
        ToolManifest(
            name="Admin",
            description="Invalid permission class.",
            kind="core",
            source=ToolSource(kind="builtin", package="openmagi.core"),
            permission="admin",
            input_schema={"type": "object"},
            timeout_ms=1_000,
        )


def test_tool_manifest_accepts_structured_policy_metadata_and_alias_dump() -> None:
    manifest = ToolManifest(
        **_base_tool_manifest_kwargs(
            capabilityTags=("workspace.read", "filesystem.inspect"),
            sideEffectClass="none",
            parallelSafety="readonly",
            emitsEvidenceTypes=("SourceInspection", "custom:WorkspaceReadTrace"),
            preconditions=("workspace mounted",),
            postconditions=("no workspace mutation",),
            transientFailureClasses=("workspace_unavailable",),
            costClass="low",
            latencyClass="interactive",
            deterministicRequirementTypes=("source_inspection",),
            canSatisfyDeterministicRequirement=True,
            adkToolType="FunctionTool",
            isConcurrencySafe=True,
        )
    )

    assert manifest.capability_tags == ("workspace.read", "filesystem.inspect")
    assert manifest.side_effect_class == "none"
    assert manifest.parallel_safety == "readonly"
    assert manifest.emits_evidence_types == ("SourceInspection", "custom:WorkspaceReadTrace")
    assert manifest.preconditions == ("workspace mounted",)
    assert manifest.postconditions == ("no workspace mutation",)
    assert manifest.transient_failure_classes == ("workspace_unavailable",)
    assert manifest.cost_class == "low"
    assert manifest.latency_class == "interactive"
    assert manifest.deterministic_requirement_types == ("source_inspection",)
    assert manifest.can_satisfy_deterministic_requirement is True
    assert manifest.adk_tool_type == "FunctionTool"

    dumped = manifest.model_dump(by_alias=True)
    assert dumped["capabilityTags"] == ("workspace.read", "filesystem.inspect")
    assert dumped["sideEffectClass"] == "none"
    assert dumped["parallelSafety"] == "readonly"
    assert dumped["emitsEvidenceTypes"] == ("SourceInspection", "custom:WorkspaceReadTrace")
    assert dumped["transientFailureClasses"] == ("workspace_unavailable",)
    assert dumped["costClass"] == "low"
    assert dumped["latencyClass"] == "interactive"
    assert dumped["deterministicRequirementTypes"] == ("source_inspection",)
    assert dumped["canSatisfyDeterministicRequirement"] is True
    assert dumped["adkToolType"] == "FunctionTool"


def test_tool_manifest_structured_policy_fields_accept_snake_case_inputs() -> None:
    manifest = ToolManifest(
        **_base_tool_manifest_kwargs(
            capability_tags=("time.lookup",),
            side_effect_class="none",
            parallel_safety="readonly",
            emits_evidence_types=("Clock",),
            deterministic_requirement_types=("clock",),
            can_satisfy_deterministic_requirement=True,
            adk_tool_type="FunctionTool",
        )
    )

    assert manifest.capability_tags == ("time.lookup",)
    assert manifest.emits_evidence_types == ("Clock",)
    assert manifest.can_satisfy_deterministic_requirement is True


def test_tool_manifest_model_copy_update_revalidates_structured_policy_metadata() -> None:
    manifest = ToolManifest(**_base_tool_manifest_kwargs(sideEffectClass="none"))

    with pytest.raises(ValidationError, match="sideEffectClass=none"):
        manifest.model_copy(update={"dangerous": True})


def test_tool_manifest_model_copy_update_revalidates_emitted_evidence_types() -> None:
    manifest = ToolManifest(**_base_tool_manifest_kwargs(emitsEvidenceTypes=("Clock",)))

    with pytest.raises(ValidationError, match="non-custom evidence types"):
        manifest.model_copy(update={"emitsEvidenceTypes": ("NotInCatalog",)})


def test_tool_manifest_accepts_canonical_builtin_clock_evidence_type() -> None:
    manifest = ToolManifest(
        **_base_tool_manifest_kwargs(
            emitsEvidenceTypes=("Clock",),
        )
    )

    assert manifest.emits_evidence_types == ("Clock",)


def test_tool_manifest_accepts_local_and_external_workspace_mutation_metadata() -> None:
    manifest = ToolManifest(
        **_base_tool_manifest_kwargs(
            mutatesWorkspace=True,
            sideEffectClass="local_and_external",
        )
    )

    assert manifest.mutates_workspace is True
    assert manifest.side_effect_class == "local_and_external"


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"sideEffectClass": "none", "dangerous": True}, "sideEffectClass=none"),
        ({"sideEffectClass": "none", "mutatesWorkspace": True}, "sideEffectClass=none"),
        (
            {"mutatesWorkspace": True, "sideEffectClass": "external"},
            "mutatesWorkspace=True",
        ),
        (
            {"mutatesWorkspace": False, "sideEffectClass": "local_workspace"},
            "sideEffectClass=.*requires mutatesWorkspace=True",
        ),
        (
            {"mutatesWorkspace": False, "sideEffectClass": "local_and_external"},
            "sideEffectClass=.*requires mutatesWorkspace=True",
        ),
        (
            {
                "mutatesWorkspace": False,
                "sideEffectClass": "local_and_external",
                "parallelSafety": "readonly",
            },
            "sideEffectClass=.*requires mutatesWorkspace=True",
        ),
        ({"parallelSafety": "readonly", "dangerous": True}, "readonly parallel-safety"),
        (
            {"parallelSafety": "readonly", "mutatesWorkspace": True},
            "readonly parallel-safety",
        ),
        (
            {"canSatisfyDeterministicRequirement": True, "emitsEvidenceTypes": ("Clock",)},
            "deterministic-capable tools",
        ),
        (
            {
                "canSatisfyDeterministicRequirement": True,
                "deterministicRequirementTypes": ("clock",),
            },
            "deterministic-capable tools",
        ),
        (
            {
                "canSatisfyDeterministicRequirement": False,
                "deterministicRequirementTypes": ("clock",),
            },
            "non-deterministic tools",
        ),
    ],
)
def test_tool_manifest_rejects_conflicting_structured_policy_metadata(
    overrides: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(ValidationError, match=match):
        ToolManifest(**_base_tool_manifest_kwargs(**overrides))


@pytest.mark.parametrize(
    "evidence_type",
    ("NotInCatalog", "custom:notPascal", "external:Ack"),
)
def test_tool_manifest_rejects_invalid_emitted_evidence_types(evidence_type: str) -> None:
    with pytest.raises(ValidationError):
        ToolManifest(
            **_base_tool_manifest_kwargs(
                emitsEvidenceTypes=(evidence_type,),
            )
        )


def test_tool_manifest_validates_long_running_function_tool_metadata() -> None:
    long_running = ToolManifest(
        **_base_tool_manifest_kwargs(
            adkToolType="LongRunningFunctionTool",
            latencyClass="background",
        )
    )

    assert long_running.adk_tool_type == "LongRunningFunctionTool"
    assert long_running.latency_class == "background"

    with pytest.raises(ValidationError, match="LongRunningFunctionTool"):
        ToolManifest(
            **_base_tool_manifest_kwargs(
                adkToolType="LongRunningFunctionTool",
                latencyClass="interactive",
                shouldDefer=False,
            )
        )

    with pytest.raises(ValidationError, match="FunctionTool"):
        ToolManifest(
            **_base_tool_manifest_kwargs(
                adkToolType="FunctionTool",
                latencyClass="long_running",
                shouldDefer=False,
            )
        )

    with pytest.raises(ValidationError, match="LongRunningFunctionTool"):
        ToolManifest(
            **_base_tool_manifest_kwargs(
                adkToolType="FunctionTool",
                latencyClass="long_running",
                shouldDefer=True,
            )
        )


def test_hook_manifest_distinguishes_security_critical_native_sources() -> None:
    manifest = HookManifest(
        name="permission_gate",
        point=HookPoint.BEFORE_TOOL_USE,
        description="Block unsafe tool calls before execution.",
        source=ToolSource(kind="native-plugin", package="openmagi.safety"),
        priority=0,
        blocking=True,
        fail_open=False,
        if_condition="Bash(*)",
        security_critical=True,
    )

    assert manifest.point is HookPoint.BEFORE_TOOL_USE
    assert manifest.source.kind == "native-plugin"
    assert manifest.fail_open is False
    assert manifest.if_condition == "Bash(*)"
    assert manifest.security_critical is True
    assert manifest.model_dump(by_alias=True)["failOpen"] is False
    assert manifest.model_dump(by_alias=True)["securityCritical"] is True
    assert manifest.model_dump(by_alias=True)["if"] == "Bash(*)"


def test_hook_result_uses_permission_decision_action_name() -> None:
    result = HookResult(action="permission_decision", decision="ask", reason="dangerous tool")

    assert result.action == "permission_decision"
    assert result.decision == "ask"


def test_plugin_manifest_models_native_cloud_capabilities_as_auditable_opt_out_units() -> None:
    manifest = PluginManifest(
        plugin_id="openmagi.cloud",
        kind=PluginKind.NATIVE,
        version="0.1.0",
        default_installed=True,
        opt_out=True,
        audit_required=True,
        capabilities=[
            PluginCapability(type="tool", name="KnowledgeSearch"),
            PluginCapability(type="hook", name="cloud_audit"),
        ],
    )

    assert manifest.kind is PluginKind.NATIVE
    assert manifest.default_installed is True
    assert manifest.opt_out is True
    assert manifest.audit_required is True
    assert [cap.type for cap in manifest.capabilities] == ["tool", "hook"]
