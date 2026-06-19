"""Local-only diagnostic shadow helpers."""

__all__ = [
    "Gate2AuditBlockReadiness",
    "Gate2AuditEvidenceOutputFlags",
    "Gate2AuditEvidenceReport",
    "Gate2AuditVerifierEntryReport",
    "Gate2ProjectedAdkEvent",
    "Gate2RecipeProfile",
    "Gate2ShadowFixtureInput",
    "Gate2ShadowFixtureReport",
    "Gate2ShadowOutputFlags",
    "Gate2MutationOutcome",
    "Gate2MutationReceipt",
    "Gate2RollbackReceipt",
    "Gate2SandboxMutationProvider",
    "Gate2ShadowToolOutputFlags",
    "Gate2ShadowToolPolicyError",
    "Gate2ShadowToolReport",
    "Gate2ShadowWorkspaceToolPolicy",
    "Gate2TextProjectedAdkEvent",
    "RedactedTypeScriptBundle",
    "load_redacted_ts_bundle",
    "compare_redacted_ts_bundle",
    "load_gate2_shadow_fixture",
    "build_gate2_audit_evidence_report",
    "resolve_gate2_recipe_profile",
    "run_gate2_shadow_fixture",
    "run_gate2_shadow_fixture_async",
    "run_gate2_recorded_tool_output",
    "run_gate2_synthetic_local_tool",
]


def __getattr__(name: str) -> object:
    if name in __all__:
        if name in {
            "Gate2AuditBlockReadiness",
            "Gate2AuditEvidenceOutputFlags",
            "Gate2AuditEvidenceReport",
            "Gate2AuditVerifierEntryReport",
            "build_gate2_audit_evidence_report",
        }:
            from . import audit_reporter

            return getattr(audit_reporter, name)
        if name in {
            "RedactedTypeScriptBundle",
            "compare_redacted_ts_bundle",
            "load_redacted_ts_bundle",
        }:
            from . import redacted_ts_bundle

            return getattr(redacted_ts_bundle, name)
        if name in {
            "Gate2RecipeProfile",
            "resolve_gate2_recipe_profile",
        }:
            from . import gate2_recipe_profile_resolver

            return getattr(gate2_recipe_profile_resolver, name)
        if name in {
            "Gate2MutationOutcome",
            "Gate2MutationReceipt",
            "Gate2RollbackReceipt",
            "Gate2SandboxMutationProvider",
            "Gate2ShadowWorkspaceToolPolicy",
        }:
            from . import gate2_shadow_tool_policy

            return getattr(gate2_shadow_tool_policy, name)
        if name in {
            "Gate2ShadowToolOutputFlags",
            "Gate2ShadowToolPolicyError",
            "Gate2ShadowToolReport",
            "run_gate2_recorded_tool_output",
            "run_gate2_synthetic_local_tool",
        }:
            from . import tool_policy

            return getattr(tool_policy, name)
        from . import fixture_runner

        return getattr(fixture_runner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
