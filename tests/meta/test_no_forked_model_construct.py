"""Meta-test (C-4 final ratchet): forbid NEW hand-written ``def model_construct``
overrides outside the single C-4 authority home ``ops/authority.py``.

After C-4 PR-A..PR-I, the single canonical force-false base is
``magi_agent.ops.authority.FalseOnlyAuthorityModel`` (and ``FrozenContractModel``
for the escape-hatch-only contract). Subclasses MUST inherit the kernel
``model_construct`` rather than re-paste their own override -- a hand-written
override is a serializer/force-false drift hazard the introspection-based
kernel exists to eliminate.

The tree still contains 232 ``def model_construct`` overrides outside the kernel
that have NOT yet been migrated in earlier C-4 passes. They are held in
documented, SHRINKING allowlists so this ratchet stays green while forbidding any
NEW override. Drive the allowlists toward empty in follow-up C-4 sweeps.

The allowlist is partitioned into three buckets for human readability -- the
meta-test itself just checks "is this (module, class) in any allowed bucket":

* ``_RAISING_OVERRIDES`` (31) -- contracts that hand-paste the escape-hatch
  disabling raise ("model_construct is disabled for ..."). These are the
  ``_FROZEN_BASE_FORK_ALLOWLIST`` peers from ``test_no_forked_digest.py`` --
  same files, same migration target (re-parent onto ``FrozenContractModel``).

* ``_DECISION_COORDINATION_OVERRIDES`` (90) -- ``*Decision`` / ``*Result`` /
  ``*Receipt`` wrapper classes whose body shapes inputs (default
  ``authorityFlags=...``, normalize aliases, coerce embedded flags) before
  routing through ``cls.model_validate``. These are PR-D/G/I-style sites where
  the routing logic is real (not pure boilerplate) and must be preserved or
  folded into a kernel hook per-site.

* ``_LEGACY_PASSTHROUGH_OVERRIDES`` (111) -- the cleanup-target follow-ups:
  legacy ``return cls()`` / ``return super().model_construct(...)`` pass-throughs
  that exist for no semantic reason (typically pre-C-4 boilerplate that the
  kernel now handles uniformly). Removing the override entirely IS the
  migration -- the kernel inherits.

The kernel home ``ops/authority.py`` itself defines TWO ``def model_construct``
(``FrozenContractModel`` and ``FalseOnlyAuthorityModel``) -- those are
required, not forbidden, and live in ``_KERNEL_FILES``.

The shrinking-allowlist guard mirrors
``test_frozen_base_allowlist_is_shrinking`` / ``test_digest_fork_allowlist_is_shrinking``:
every allowlist entry must (a) still resolve to a class in the tree and
(b) still actually override ``model_construct`` -- so migrated/removed sites
are pruned and cannot silently mask a re-fork.
"""

from __future__ import annotations

import ast
from pathlib import Path

_MAGI_ROOT = Path(__file__).resolve().parents[2] / "magi_agent"

# The canonical home. Defining ``model_construct`` HERE is required, not
# forbidden (``FrozenContractModel`` disables the escape hatch;
# ``FalseOnlyAuthorityModel`` routes through ``model_validate`` so the
# force-false invariant applies uniformly).
_KERNEL_FILES = {
    "ops/authority.py",
}


# ---------------------------------------------------------------------------
# SHRINKING ALLOWLIST -- partitioned by override shape for human readability.
# The meta-test itself unions these into a single set; the partitioning is
# purely documentary.
# ---------------------------------------------------------------------------

# (1) Hand-pasted escape-hatch disabling raises ("model_construct is disabled
# for ..."). These overlap with the ``_FROZEN_BASE_FORK_ALLOWLIST`` in
# test_no_forked_digest.py -- same files, same migration target (re-parent onto
# ``FrozenContractModel``). Migration removes the override.
_RAISING_OVERRIDES: frozenset[tuple[str, str]] = frozenset(
    {
        ("coding/meta_adapter.py", "_CodingMetaModel"),
        ("connectors/credential_lease.py", "_LeaseModel"),
        ("connectors/registry.py", "_ConnectorModel"),
        ("evidence/child_runtime_envelope.py", "ChildRuntimeEnvelope"),
        ("evidence/runtime_issuance.py", "RuntimeIssueAuthority"),
        ("meta_orchestration/child_acceptance.py", "_ChildAcceptanceModel"),
        ("meta_orchestration/child_roles.py", "_MetaChildRoleModel"),
        ("meta_orchestration/commit_adapter.py", "_MetaBeforeCommitModel"),
        ("meta_orchestration/final_assembly.py", "_MetaFinalAssemblyModel"),
        ("meta_orchestration/inspection_loop.py", "_MetaInspectionModel"),
        ("meta_orchestration/projection.py", "_MetaProjectionModel"),
        ("meta_orchestration/task_plan.py", "_MetaTaskPlanModel"),
        ("packs/types.py", "_CatalogModel"),
        ("permissions/auto_control.py", "_SealedPermissionRecord"),
        ("research/acceptance_criteria.py", "_ResearchAcceptanceModel"),
        ("research/action_claims.py", "_ResearchActionModel"),
        ("research/boundary_enforcement.py", "_ResearchBoundaryModel"),
        ("research/child_roles.py", "_ResearchChildModel"),
        ("research/claim_graph.py", "_ResearchClaimModel"),
        ("research/evidence_graph.py", "_ResearchEvidenceModel"),
        ("research/final_projection_gate.py", "_ResearchFinalProjectionModel"),
        ("research/meta_adapter.py", "_ResearchMetaModel"),
        ("research/output_contract_gate.py", "_OutputContractModel"),
        ("research/policy_pack.py", "_ResearchPolicyPackModel"),
        ("research/repair.py", "_ResearchRepairModel"),
        ("research/source_proof.py", "_ResearchSourceModel"),
        ("runtime/heartbeat_contract.py", "_RuntimeHeartbeatModel"),
        ("sandbox/policy.py", "_SandboxModel"),
        ("security/compliance.py", "ComplianceAuthorityFlags"),
        ("telemetry/deterministic_events.py", "_FrozenNoUpdateModel"),
        ("web_acquisition/repo_research_tools.py", "RepoResearchSourceRecord"),
    }
)

# (2) ``*Decision`` / ``*Result`` / ``*Receipt`` wrapper classes whose
# ``model_construct`` body shapes input (defaults ``authorityFlags=...``,
# normalizes aliases, coerces embedded flags) before routing through
# ``cls.model_validate``. PR-D/G/I-style sites: the routing logic is real
# (not pure boilerplate) and must be preserved -- migration is a per-site
# decision (fold into a kernel hook, or keep behind a documented adapter).
_DECISION_COORDINATION_OVERRIDES: frozenset[tuple[str, str]] = frozenset(
    {
        ("artifacts/delivery_boundary.py", "ArtifactChannelDeliveryDecision"),
        ("artifacts/delivery_receipts.py", "ArtifactDeliveryReceipt"),
        ("artifacts/file_delivery.py", "FileDeliveryDecision"),
        ("artifacts/output_registry_boundary.py", "OutputArtifactRegistryDecision"),
        ("artifacts/render_verification.py", "RenderVerificationReceipt"),
        ("browser/live_provider_pack.py", "BrowserProviderPackResult"),
        ("browser/provider_boundary.py", "BrowserProviderResult"),
        ("channels/discord_adapter.py", "DiscordAdapterDecision"),
        ("channels/dispatcher.py", "ChannelDispatchDecision"),
        ("channels/push_delivery.py", "PushDeliveryDecision"),
        ("channels/runtime_boundary.py", "ChannelRuntimeDecision"),
        ("channels/telegram_adapter.py", "TelegramAdapterDecision"),
        ("channels/telegram_boundary.py", "TelegramRuntimeDecision"),
        ("config/models.py", "PythonGate8ReadinessConfig"),
        ("config/models.py", "PythonRuntimeAuthorityConfig"),
        ("harness/coding/code_intelligence_contracts.py", "CodeIntelligenceReport"),
        ("harness/coding/ownership_projection.py", "CodingRecipeOwnershipProjection"),
        ("harness/discipline_boundary.py", "DisciplineDecision"),
        ("knowledge/provider_boundary.py", "KnowledgeBoundaryDecision"),
        ("memory/policy.py", "MemoryProjectionGateDecision"),
        ("memory/projection.py", "SanitizedMemoryReference"),
        ("memory/projection.py", "SourceAuthorityEnvelope"),
        ("memory/recall_ledger.py", "_RecallLedgerModel"),
        ("memory/write_boundary.py", "MemoryMutationReceipt"),
        ("memory/write_boundary.py", "MemoryMutationTarget"),
        ("missions/cron_policy.py", "CronMutationPolicy"),
        ("missions/cron_policy.py", "CronMutationRequest"),
        ("missions/cron_policy.py", "CronSchedulerMutationConfig"),
        ("missions/cron_policy.py", "CronSchedulerMutationReceipt"),
        ("missions/cron_policy.py", "CronSchedulerMutationResult"),
        ("missions/events.py", "MissionEventProjectionConfig"),
        ("missions/events.py", "MissionPublicEventProjectionResult"),
        ("missions/events.py", "MissionRuntimeEventRequest"),
        ("missions/lifecycle.py", "MissionLifecycleConfig"),
        ("missions/lifecycle.py", "MissionLifecyclePolicy"),
        ("missions/lifecycle.py", "MissionTransitionResult"),
        ("missions/receipts.py", "MissionTransitionReceipt"),
        ("plugins/extension_boundary.py", "ExtensionBoundaryDecision"),
        ("plugins/shell_testrun_safe_subset.py", "ShellTestRunAuthorityFlags"),
        ("plugins/shell_testrun_safe_subset.py", "ShellTestRunDecision"),
        ("recipes/coding_evidence_gate.py", "CodingEvidenceGateDecision"),
        ("recipes/coding_mutation.py", "CodingMutationDecision"),
        ("recipes/coding_subagents.py", "CodingSubagentResult"),
        ("recipes/effective_contract.py", "EffectiveRecipeContract"),
        ("recipes/first_party/coding/ownership.py", "CodingMechanicOwnership"),
        ("recipes/first_party/coding/ownership.py", "CodingRecipeOwnershipManifest"),
        ("recipes/first_party/memory_recall.py", "MemoryRecallAuthorityFlags"),
        ("recipes/first_party/memory_recall.py", "MemoryRecallProjectionPolicy"),
        ("recipes/first_party/self_improvement.py", "SelfImprovementProposalRecipeManifest"),
        ("recipes/hook_composition.py", "EffectiveRecipeHookContract"),
        ("recipes/merge_algebra.py", "EffectiveRecipeMergeContract"),
        ("recipes/merge_algebra.py", "RetryMergePolicy"),
        ("recipes/projection.py", "RecipeCompositionProjection"),
        ("recipes/research_child_runner.py", "ResearchChildRunnerResult"),
        ("runtime/child_runner_boundary.py", "ChildRunnerEnvelopeRef"),
        ("runtime/child_runner_boundary.py", "ChildRunnerResult"),
        ("runtime/content_replacement.py", "ContentReplacement"),
        ("runtime/context_lifecycle.py", "ContextCompactionDecision"),
        ("runtime/context_lifecycle.py", "ContextRestoreResult"),
        ("runtime/events.py", "NormalizedEvent"),
        ("runtime/long_running_activity.py", "LongRunningActivityConfig"),
        ("runtime/long_running_activity.py", "LongRunningActivityPolicy"),
        ("runtime/long_running_activity.py", "LongRunningActivityReceipt"),
        ("runtime/long_running_activity.py", "LongRunningActivityRequest"),
        ("runtime/long_running_activity.py", "LongRunningActivityResult"),
        ("runtime/no_agent_watchdog.py", "NoAgentWatchdogDecision"),
        ("runtime/provider_execution.py", "ProviderExecutionResult"),
        ("runtime/provider_receipts.py", "ProviderReceipt"),
        ("runtime/slash_control_boundary.py", "SlashControlDecision"),
        ("runtime/structured_output_boundary.py", "StructuredOutputDecision"),
        ("security/compliance.py", "PolicyKernelDecisionRecord"),
        ("security/compliance.py", "_ComplianceModel"),
        ("shadow/gate2_recipe_profile_resolver.py", "_Gate2ProfileModel"),
        ("shadow/gate2_shadow_tool_policy.py", "_Gate2PolicyModel"),
        ("shadow/research_runner_capture.py", "ResearchArtifactRow"),
        ("storage/content_addressed.py", "_ContentModel"),
        ("storage/durable_store.py", "CorruptionReport"),
        ("storage/durable_store.py", "DurableStoreBackupContract"),
        ("storage/durable_store.py", "DurableStoreConfig"),
        ("storage/durable_store.py", "DurableStoreReceipt"),
        ("storage/durable_store.py", "HostedDurableStoreAdapterBoundary"),
        ("storage/durable_store.py", "ReplayDecision"),
        ("storage/durable_store.py", "RuntimeMetadataIndexRecord"),
        ("tools/read_ledger.py", "WorkspaceMutationReadDecision"),
        ("web_acquisition/acquisition_plan.py", "_PlanModel"),
        ("web_acquisition/provider_boundary.py", "WebAcquisitionResult"),
        ("web_acquisition/reference_research_tools.py", "ReferenceResearchAuthorityFlags"),
        ("web_acquisition/reference_research_tools.py", "ReferenceResearchConfig"),
        ("web_acquisition/repo_research_tools.py", "RepoResearchResult"),
        ("workspace/adoption_boundary.py", "WorkspaceMutationConfig"),
        ("workspace/adoption_boundary.py", "WorkspaceMutationDecision"),
    }
)

# (3) Legacy ``return cls()`` / ``return super().model_construct(...)``
# pass-throughs that exist for no semantic reason. The cleanup is to DELETE
# the override (the kernel inherits). These are the biggest follow-up
# opportunity -- mechanical removals once the parent base is on the C-4
# kernel.
_LEGACY_PASSTHROUGH_OVERRIDES: frozenset[tuple[str, str]] = frozenset(
    {
        ("adk_bridge/artifact_service.py", "ArtifactAuthorityFlags"),
        ("adk_bridge/artifact_service.py", "ArtifactBoundaryConfig"),
        ("adk_bridge/memory_service.py", "MemoryAuthorityFlags"),
        ("adk_bridge/memory_service.py", "MemoryBoundaryConfig"),
        ("artifacts/local_result_store.py", "LocalResultStoreReceipt"),
        ("browser/live_provider_pack.py", "BrowserProviderPackAuthorityFlags"),
        ("browser/live_provider_pack.py", "BrowserProviderPackConfig"),
        ("evidence/observed_egress.py", "ObservedEgressEvidence"),
        ("evidence/validator_taxonomy.py", "ValidatorPolicy"),
        ("gates/api_canary_ladder.py", "_CanaryLadderModel"),
        ("gates/gate1a_readonly_tools.py", "_Gate1AModel"),
        ("gates/gate5b_full_toolhost.py", "_Gate5BFullModel"),
        ("gates/pregate8_continuity_canary.py", "_PreGate8ContinuityCanaryModel"),
        ("harness/coding/code_intelligence_contracts.py", "CodeIntelligenceAuthorityFlags"),
        ("harness/general_automation/background_task_projection.py", "BackgroundTaskProjectionAuthorityFlags"),
        ("harness/general_automation/browser_evidence.py", "BrowserEvidenceAuthorityFlags"),
        ("harness/general_automation/control_projection.py", "GeneralAutomationControlAuthorityFlags"),
        ("harness/general_automation/event_projection.py", "GeneralAutomationEventAuthorityFlags"),
        ("harness/general_automation/output_budget_policy.py", "OutputReferenceAuthorityFlags"),
        ("harness/general_automation/shell_policy.py", "ShellPolicyAuthorityFlags"),
        ("harness/general_automation/spreadsheet_evidence.py", "SpreadsheetEvidenceAuthorityFlags"),
        ("knowledge/provider_boundary.py", "KnowledgeAuthorityFlags"),
        ("missions/cron_policy.py", "CronSchedulerMutationAuthorityFlags"),
        ("missions/events.py", "MissionEventProjectionAuthorityFlags"),
        ("missions/receipts.py", "MissionLifecycleAuthorityFlags"),
        ("permissions/auto_control.py", "AutoPermissionAuthorityFlags"),
        ("plugins/extension_boundary.py", "ExtensionAuthorityFlags"),
        ("plugins/general_automation/hook_projection.py", "PluginLifecycleAuthorityFlags"),
        ("plugins/general_automation/mcp_projection.py", "McpProjectionAuthorityFlags"),
        ("plugins/mcp_adapter.py", "McpAuthorityFlags"),
        ("plugins/mcp_adapter.py", "McpCallDecision"),
        ("plugins/mcp_adapter.py", "McpListDecision"),
        ("plugins/mcp_adapter.py", "McpPromptListDecision"),
        ("plugins/mcp_adapter.py", "McpPromptResolveDecision"),
        ("recipes/compiler.py", "MissionLifecycleMetadata"),
        ("recipes/compiler.py", "RecipeAttachmentFlags"),
        ("recipes/compiler.py", "_FrozenRecipeModel"),
        ("recipes/composition.py", "AdmittedRecipeSnapshot"),
        ("recipes/composition.py", "RecipeAdmissionResult"),
        ("recipes/first_party/general_automation/background_task_contracts.py", "BackgroundTaskResumeAuthorityFlags"),
        ("recipes/first_party/general_automation/browser_contracts.py", "BrowserBoundaryAuthorityFlags"),
        ("recipes/first_party/general_automation/spreadsheet_contracts.py", "SpreadsheetContractAuthorityFlags"),
        ("recipes/first_party/general_automation/web_acquisition_contracts.py", "WebAcquisitionAuthorityFlags"),
        ("recipes/first_party/self_improvement.py", "SelfImprovementRecipeAttachmentFlags"),
        ("runtime/adk_turn_runner.py", "AdkTurnAuthority"),
        ("runtime/adk_turn_runner.py", "AdkTurnProductionWrites"),
        ("runtime/adk_turn_runner.py", "AdkTurnResult"),
        ("runtime/approval_resume.py", "_ApprovalResumeModel"),
        ("runtime/cache_safe_params.py", "CacheSafeParams"),
        ("runtime/child_runner_boundary.py", "ChildRunnerAuthorityFlags"),
        ("runtime/context_lifecycle.py", "ContextLifecycleAuthorityFlags"),
        ("runtime/context_packet.py", "ContextContinuityAuthorityFlags"),
        ("runtime/context_packet.py", "_ContextPacketModel"),
        ("runtime/deterministic_policy.py", "RuntimeInvariantSet"),
        ("runtime/long_running_activity.py", "LongRunningActivityAuthorityFlags"),
        ("runtime/model_tiers.py", "_StrictModel"),
        ("runtime/no_agent_watchdog.py", "NoAgentWatchdogAuthorityFlags"),
        ("runtime/provider_execution.py", "ProviderExecutionAuthorityFlags"),
        ("runtime/query_state.py", "QueryStateAuthorityFlags"),
        ("runtime/query_state.py", "_Pr21Model"),
        ("runtime/readiness.py", "PriorityAReadinessAuthorityFlags"),
        ("runtime/readiness.py", "RuntimeHeartbeatReadinessSnapshot"),
        ("runtime/readiness.py", "_PriorityAReadinessModel"),
        ("runtime/request_ledger.py", "RequestLedgerAuthorityFlags"),
        ("runtime/request_ledger.py", "_RequestLedgerModel"),
        ("runtime/session_continuity.py", "SessionContinuityAuthorityFlags"),
        ("runtime/session_continuity.py", "_ContinuityModel"),
        ("runtime/session_continuity_projection.py", "_ProjectionModel"),
        ("runtime/slash_control_boundary.py", "SlashControlAuthorityFlags"),
        ("runtime/structured_output_boundary.py", "StructuredOutputAuthorityFlags"),
        ("shadow/fact_grounding_verifier_contract.py", "FactGroundingVerifierAttachmentFlags"),
        ("shadow/gate3b_local_consumer.py", "Gate3BLocalConsumerAttachmentFlags"),
        ("shadow/gate3b_local_report.py", "Gate3BLocalReportAttachmentFlags"),
        ("shadow/gate3b_local_report.py", "_Gate3BLocalReportModel"),
        ("shadow/gate3b_metrics.py", "Gate3BLocalMetricAttachmentFlags"),
        ("shadow/gate3b_metrics.py", "_Gate3BLocalMetricsModel"),
        ("shadow/gate4_bridge.py", "Gate4LocalBridgeAttachmentFlags"),
        ("shadow/gate4_consumer.py", "Gate4LocalConsumerAttachmentFlags"),
        ("shadow/gate4c0_shadow_config.py", "Gate4C0AuthorityFlags"),
        ("shadow/gate4c0_shadow_config.py", "_Gate4C0Model"),
        ("shadow/gate4c1_dry_run_boundary.py", "Gate4C1DryRunBoundaryFlags"),
        ("shadow/gate4c1_runner_shadow_invoker.py", "Gate4C1RunnerAuthorityFlags"),
        ("shadow/gate4c2_shadow_comparison_report.py", "Gate4C2AuthorityFlags"),
        ("shadow/gate4d_local_shadow_diagnostics.py", "Gate4DShadowAuthorityFlags"),
        ("shadow/gate5a_no_memory_shadow_canary.py", "Gate5ANoMemoryShadowCanaryAuthorityFlags"),
        ("shadow/gate5a_no_memory_shadow_canary.py", "Gate5ANoMemoryShadowCanaryPolicy"),
        ("shadow/gate5a_no_memory_shadow_canary.py", "_Gate5AModel"),
        ("shadow/gate5b4_internal_endpoint_contract.py", "Gate5B4EndpointAuthorityFlags"),
        ("shadow/gate5b4_internal_endpoint_contract.py", "_Gate5B4Model"),
        ("shadow/gate5b4c2_shadow_invocation_contract.py", "Gate5B4C2ShadowAuthorityFlags"),
        ("shadow/gate5b4c2_shadow_invocation_contract.py", "_Gate5B4C2Model"),
        ("shadow/gate5b4c3_live_runner_boundary.py", "Gate5B4C3LiveRunnerBoundaryResult"),
        ("shadow/gate5b4c3_runner_input_adapter.py", "_Gate5B4C3RunnerInputModel"),
        ("shadow/gate5b4c3_shadow_comparison.py", "_Gate5B4C3ComparisonModel"),
        ("shadow/gate5b4c3_shadow_counter_store.py", "_Gate5B4C3CounterModel"),
        ("shadow/gate5b4c3_shadow_generation_contract.py", "Gate5B4C3ShadowGenerationAuthorityFlags"),
        ("shadow/gate5b4c3_shadow_generation_contract.py", "_Gate5B4C3Model"),
        ("shadow/gate5b4c3_shadow_generation_report.py", "_Gate5B4C3RunnerReportModel"),
        ("shadow/gate5b_user_visible_routing_canary.py", "Gate5BNoMemoryRoutingCanaryAuthorityFlags"),
        ("shadow/gate5b_user_visible_routing_canary.py", "Gate5BNoMemoryRoutingCanaryPolicy"),
        ("shadow/gate5b_user_visible_routing_canary.py", "_Gate5BModel"),
        ("shadow/research_runner_capture.py", "ResearchArtifactAuthorityFlags"),
        ("shadow/ts_parity_replay.py", "TsParityReplayAttachmentFlags"),
        ("shadow/workspace_adoption_preflight_contract.py", "WorkspaceAdoptionPreflightAttachmentFlags"),
        ("tools/kernel.py", "_ToolKernelModel"),
        ("tools/output_budget.py", "BudgetedToolResult"),
        ("tools/schema_validation.py", "ToolSchemaValidationDecision"),
        ("web_acquisition/live_provider_pack.py", "WebAcquisitionProviderAuthorityFlags"),
        ("web_acquisition/live_provider_pack.py", "WebAcquisitionProviderPackConfig"),
        ("web_acquisition/opencode_provider_router.py", "OpenCodeWebProviderRouterDecision"),
        ("workspace/adoption_boundary.py", "WorkspaceMutationAuthorityFlags"),
    }
)

# Union: the meta-test checks "is this (module, class) in any allowed bucket".
_MODEL_CONSTRUCT_FORK_ALLOWLIST: frozenset[tuple[str, str]] = (
    _RAISING_OVERRIDES | _DECISION_COORDINATION_OVERRIDES | _LEGACY_PASSTHROUGH_OVERRIDES
)


def _iter_modules() -> list[Path]:
    """Walk ``magi_agent/*.py``. Filter ``__pycache__`` and any directory whose
    name is exactly ``tests`` or ``test`` (substring match would falsely exclude
    legitimate files like ``plugins/shell_testrun_safe_subset.py``)."""
    return sorted(
        path
        for path in _MAGI_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
        and not any(part in {"tests", "test"} for part in path.parts)
    )


def _classes_with_model_construct() -> set[tuple[str, str]]:
    """AST-walk every magi_agent module and collect ``(relative_path, class_name)``
    pairs where the class body defines a method named ``model_construct``.

    AST-based (not regex) so docstring mentions / commented-out defs / strings
    containing ``"def model_construct"`` are not false positives.
    """
    found: set[tuple[str, str]] = set()
    for path in _iter_modules():
        rel = path.relative_to(_MAGI_ROOT).as_posix()
        if rel in _KERNEL_FILES:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:  # pragma: no cover - defensive
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == "model_construct"
                ):
                    found.add((rel, node.name))
                    break
    return found


def test_no_forked_model_construct() -> None:
    """Ratchet: no NEW hand-written ``def model_construct`` may appear outside
    the C-4 kernel (``ops/authority.py``) that is not on the shrinking allowlist.

    To satisfy this, route new authority code through
    ``magi_agent.ops.authority.FalseOnlyAuthorityModel`` (force-false fields via
    ``Literal[False]``) or ``FrozenContractModel`` (escape-hatch disabled), and
    let the kernel ``model_construct`` apply. Re-paste of the override defeats
    the C-4 introspection-based force-false invariant.
    """
    offenders = sorted(_classes_with_model_construct() - _MODEL_CONSTRUCT_FORK_ALLOWLIST)
    assert not offenders, (
        "New forked `def model_construct` override(s) found outside "
        "ops/authority.py. Subclass magi_agent.ops.authority.FalseOnlyAuthorityModel "
        "(or FrozenContractModel) and inherit the kernel `model_construct`, "
        "or -- only if genuinely tricky -- add a justified entry to the "
        "appropriate bucket in test_no_forked_model_construct.py.\n"
        + "\n".join(f"  {rel}::{cls}" for rel, cls in offenders)
    )


def test_model_construct_allowlist_is_shrinking() -> None:
    """Ratchet hygiene: every allowlist entry must (a) still resolve to a class
    in the tree and (b) still actually define ``model_construct``. Migrated /
    removed sites are pruned so a re-fork cannot silently slip in under a stale
    entry.

    Same pattern as ``test_frozen_base_allowlist_is_shrinking`` in
    ``tests/meta/test_no_forked_digest.py``.
    """
    found = _classes_with_model_construct()
    stale = sorted(_MODEL_CONSTRUCT_FORK_ALLOWLIST - found)
    assert not stale, (
        "Stale model_construct allowlist entries (class gone or no longer "
        "overrides model_construct -- remove from the matching bucket "
        "(_RAISING_OVERRIDES / _DECISION_COORDINATION_OVERRIDES / "
        "_LEGACY_PASSTHROUGH_OVERRIDES) in test_no_forked_model_construct.py):\n"
        + "\n".join(f"  {rel}::{cls}" for rel, cls in stale)
    )


def test_allowlist_buckets_are_disjoint() -> None:
    """Hygiene: a single (module, class) should appear in at most one bucket
    -- otherwise the documentary categorization is wrong (the body shape should
    be unambiguous: raise XOR return-validate XOR return-cls)."""
    pairs = [
        ("_RAISING_OVERRIDES", _RAISING_OVERRIDES),
        ("_DECISION_COORDINATION_OVERRIDES", _DECISION_COORDINATION_OVERRIDES),
        ("_LEGACY_PASSTHROUGH_OVERRIDES", _LEGACY_PASSTHROUGH_OVERRIDES),
    ]
    overlaps: list[str] = []
    for i, (a_name, a) in enumerate(pairs):
        for b_name, b in pairs[i + 1 :]:
            common = sorted(a & b)
            if common:
                overlaps.append(
                    f"{a_name} & {b_name}: "
                    + ", ".join(f"{rel}::{cls}" for rel, cls in common)
                )
    assert not overlaps, "Allowlist bucket overlap:\n" + "\n".join(overlaps)
