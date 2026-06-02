from openmagi_core_agent.authoring.contracts import (
    BuilderAnswer,
    BuilderGap,
    BuilderGapReport,
    BuilderPhase,
    BuilderQuestion,
    BuilderReviewSummary,
    DraftApprovalPolicy,
    DraftBudgetPolicy,
    DraftEvidencePolicy,
    DraftHardInvariant,
    DraftHarnessPolicy,
    DraftProjectionPolicy,
    DraftRecipePack,
    DraftRepairPolicy,
    DraftToolPolicy,
    DraftValidatorPolicy,
    EvalFixtureSet,
    GeneratedPluginProposal,
    RecipePackDraft,
    RecipePackVersion,
    RecipeBuilderSession,
)
from openmagi_core_agent.authoring.compiler import (
    CompileRecipePackCatalog,
    CompileRecipePackDiagnostic,
    CompileRecipePackResult,
    HardInvariantResult,
    compile_recipe_pack,
)
from openmagi_core_agent.authoring.dry_run import (
    DryRunRecipePackCatalog,
    DryRunRecipePackConfig,
    DryRunRecipePackRequest,
    DryRunRecipePackResult,
    DryRunRecipePackWarning,
    dry_run_recipe_pack,
)

_TOOL_CONTRACT_EXPORTS = {
    "CompileRecipePack",
    "DraftEvalFixtures",
    "DraftHarnessPolicyTool",
    "DraftRecipePackTool",
    "DryRunRecipePack",
    "GenerateActivationPlan",
    "GenerateGapReport",
    "InspectConnectorAvailability",
    "InspectHarnessRegistry",
    "InspectPluginCatalog",
    "InspectRecipeRegistry",
    "InspectToolCatalog",
    "InspectValidatorRegistry",
    "ReadMagiDocs",
    "SaveRecipePackDraft",
    "run_compile_recipe_pack",
    "run_dry_run_recipe_pack",
    "run_generate_activation_plan",
}
_HARNESS_EXPORTS = {
    "RecipeBuilderModeConfig",
    "RecipeBuilderModeState",
    "advance_recipe_builder_mode",
}
_STORAGE_EXPORTS = {
    "CompiledSnapshotRef",
    "EvalResultRef",
    "GeneratedPluginProposalArtifactRef",
    "LocalRecipePackStorage",
    "PromotionHistoryEntry",
    "RecipePackApprovalRef",
    "RecipePackDraftRecord",
    "RecipePackStorageError",
    "RecipePackVersionRecord",
    "digest_storage_content",
}
_PROJECTION_EXPORTS = {
    "RecipeBuilderProjection",
    "build_recipe_builder_projection",
}
_GENERATED_PROPOSAL_EXPORTS = {
    "GeneratedProposalArtifactFileRef",
    "GeneratedProposalDigestSummaryRef",
    "GeneratedProposalExecutionDefault",
    "GeneratedProposalManifest",
    "GeneratedProposalSandboxPlanRef",
    "GeneratedProposalSourceRef",
    "digest_generated_proposal_manifest",
}
_EXPORT_PACKAGE_EXPORTS = {
    "RecipeExportGeneratedProposalRef",
    "RecipeExportPackageArtifactRef",
    "RecipeExportPackageManifest",
    "RecipeExportPackageScope",
    "RecipeExportPackageSubjectRef",
    "RecipeImportValidationBlocker",
    "RecipeImportValidationRequest",
    "RecipeImportValidationResult",
    "digest_recipe_export_package_manifest",
    "validate_recipe_export_package_import",
}
_BACKUP_RESTORE_EXPORTS = {
    "RecipeBackupArtifactRef",
    "RecipeBackupLedgerRef",
    "RecipeBackupManifest",
    "RecipeBackupScope",
    "RecipeRestoreValidationBlocker",
    "RecipeRestoreValidationRequest",
    "RecipeRestoreValidationResult",
    "digest_recipe_backup_manifest",
    "validate_recipe_restore_request",
}
_AUDIT_EVENT_EXPORTS = {
    "RecipeBuilderAuditBatch",
    "RecipeBuilderAuditEvent",
    "RecipeBuilderAuditEventRef",
    "RecipeBuilderAuditEventType",
    "RecipeBuilderAuditRedactionStatus",
    "RecipeBuilderAuditScope",
    "RecipeBuilderAuditValidationResult",
    "digest_recipe_builder_audit_batch",
    "digest_recipe_builder_audit_event",
    "validate_recipe_builder_audit_batch",
}


def __getattr__(name: str):
    if name in _TOOL_CONTRACT_EXPORTS:
        from importlib import import_module

        module = import_module("openmagi_core_agent.authoring.tool_contracts")
        value = getattr(module, name)
        globals()[name] = value
        return value
    if name in _HARNESS_EXPORTS:
        from importlib import import_module

        module = import_module("openmagi_core_agent.authoring.harness")
        value = getattr(module, name)
        globals()[name] = value
        return value
    if name in _STORAGE_EXPORTS:
        from importlib import import_module

        module = import_module("openmagi_core_agent.authoring.storage")
        value = getattr(module, name)
        globals()[name] = value
        return value
    if name in _PROJECTION_EXPORTS:
        from importlib import import_module

        module = import_module("openmagi_core_agent.authoring.projection")
        value = getattr(module, name)
        globals()[name] = value
        return value
    if name in _GENERATED_PROPOSAL_EXPORTS:
        from importlib import import_module

        module = import_module("openmagi_core_agent.authoring.generated_proposals")
        value = getattr(module, name)
        globals()[name] = value
        return value
    if name in _EXPORT_PACKAGE_EXPORTS:
        from importlib import import_module

        module = import_module("openmagi_core_agent.authoring.export_package")
        value = getattr(module, name)
        globals()[name] = value
        return value
    if name in _BACKUP_RESTORE_EXPORTS:
        from importlib import import_module

        module = import_module("openmagi_core_agent.authoring.backup_restore")
        value = getattr(module, name)
        globals()[name] = value
        return value
    if name in _AUDIT_EVENT_EXPORTS:
        from importlib import import_module

        module = import_module("openmagi_core_agent.authoring.audit_events")
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(__all__))

__all__ = [
    "BuilderAnswer",
    "BuilderGap",
    "BuilderGapReport",
    "BuilderPhase",
    "BuilderQuestion",
    "BuilderReviewSummary",
    "CompileRecipePackCatalog",
    "CompileRecipePackDiagnostic",
    "CompileRecipePackResult",
    "DraftApprovalPolicy",
    "DraftBudgetPolicy",
    "DraftEvidencePolicy",
    "DraftHardInvariant",
    "DraftHarnessPolicy",
    "DraftProjectionPolicy",
    "DraftRecipePack",
    "DraftRepairPolicy",
    "DraftToolPolicy",
    "DraftValidatorPolicy",
    "DryRunRecipePackCatalog",
    "DryRunRecipePackConfig",
    "DryRunRecipePackRequest",
    "DryRunRecipePackResult",
    "DryRunRecipePackWarning",
    "CompileRecipePack",
    "DraftEvalFixtures",
    "DraftHarnessPolicyTool",
    "DraftRecipePackTool",
    "DryRunRecipePack",
    "GenerateActivationPlan",
    "GenerateGapReport",
    "InspectConnectorAvailability",
    "InspectHarnessRegistry",
    "InspectPluginCatalog",
    "InspectRecipeRegistry",
    "InspectToolCatalog",
    "InspectValidatorRegistry",
    "ReadMagiDocs",
    "SaveRecipePackDraft",
    "run_compile_recipe_pack",
    "run_dry_run_recipe_pack",
    "run_generate_activation_plan",
    "EvalFixtureSet",
    "GeneratedPluginProposal",
    "HardInvariantResult",
    "RecipePackDraft",
    "RecipePackVersion",
    "RecipeBuilderSession",
    "RecipeBuilderModeConfig",
    "RecipeBuilderModeState",
    "advance_recipe_builder_mode",
    "CompiledSnapshotRef",
    "EvalResultRef",
    "GeneratedPluginProposalArtifactRef",
    "LocalRecipePackStorage",
    "PromotionHistoryEntry",
    "RecipePackApprovalRef",
    "RecipePackDraftRecord",
    "RecipePackStorageError",
    "RecipePackVersionRecord",
    "RecipeBuilderProjection",
    "build_recipe_builder_projection",
    "GeneratedProposalArtifactFileRef",
    "GeneratedProposalDigestSummaryRef",
    "GeneratedProposalExecutionDefault",
    "GeneratedProposalManifest",
    "GeneratedProposalSandboxPlanRef",
    "GeneratedProposalSourceRef",
    "RecipeExportGeneratedProposalRef",
    "RecipeExportPackageArtifactRef",
    "RecipeExportPackageManifest",
    "RecipeExportPackageScope",
    "RecipeExportPackageSubjectRef",
    "RecipeBackupArtifactRef",
    "RecipeBackupLedgerRef",
    "RecipeBackupManifest",
    "RecipeBackupScope",
    "RecipeImportValidationBlocker",
    "RecipeImportValidationRequest",
    "RecipeImportValidationResult",
    "RecipeRestoreValidationBlocker",
    "RecipeRestoreValidationRequest",
    "RecipeRestoreValidationResult",
    "RecipeBuilderAuditBatch",
    "RecipeBuilderAuditEvent",
    "RecipeBuilderAuditEventRef",
    "RecipeBuilderAuditEventType",
    "RecipeBuilderAuditRedactionStatus",
    "RecipeBuilderAuditScope",
    "RecipeBuilderAuditValidationResult",
    "digest_generated_proposal_manifest",
    "digest_recipe_builder_audit_batch",
    "digest_recipe_builder_audit_event",
    "digest_recipe_backup_manifest",
    "digest_recipe_export_package_manifest",
    "digest_storage_content",
    "compile_recipe_pack",
    "dry_run_recipe_pack",
    "validate_recipe_restore_request",
    "validate_recipe_builder_audit_batch",
    "validate_recipe_export_package_import",
]
