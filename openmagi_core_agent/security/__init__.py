from __future__ import annotations

from openmagi_core_agent.security.advisory import (
    Advisory,
    AdvisoryFinding,
    LazyDependencyDecision,
    LazyDependencyPolicy,
    LazyDependencyRequest,
    check_installed_advisories,
    evaluate_lazy_dependency_request,
)
from openmagi_core_agent.security.credentials import (
    CredentialDecision,
    CredentialPassThroughPolicy,
    CredentialRequest,
    evaluate_credential_request,
)
from openmagi_core_agent.security.context_guard import (
    ContextGuardResult,
    scan_context_file,
)
from openmagi_core_agent.security.compliance import (
    ComplianceAuthorityFlags,
    ComplianceReportRef,
    PolicyKernelDecisionRecord,
    RollbackFallbackDiagnosticRef,
    build_compliance_report_ref,
    record_policy_kernel_decision,
)
from openmagi_core_agent.security.external_surface import (
    ExternalSurfaceDecision,
    ExternalSurfacePolicy,
    ExternalSurfaceRequest,
    evaluate_external_surface,
)
from openmagi_core_agent.security.posture import (
    SecurityControl,
    SecurityPostureDecision,
    SecurityPostureRequest,
    evaluate_security_posture,
)
from openmagi_core_agent.security.sandbox_preflight import (
    SandboxPreflightReport,
    SandboxPreflightRequest,
    evaluate_sandbox_preflight,
)


__all__ = [
    "Advisory",
    "AdvisoryFinding",
    "ComplianceAuthorityFlags",
    "ComplianceReportRef",
    "CredentialDecision",
    "CredentialPassThroughPolicy",
    "CredentialRequest",
    "ContextGuardResult",
    "ExternalSurfaceDecision",
    "ExternalSurfacePolicy",
    "ExternalSurfaceRequest",
    "LazyDependencyDecision",
    "LazyDependencyPolicy",
    "LazyDependencyRequest",
    "PolicyKernelDecisionRecord",
    "RollbackFallbackDiagnosticRef",
    "SandboxPreflightReport",
    "SandboxPreflightRequest",
    "SecurityControl",
    "SecurityPostureDecision",
    "SecurityPostureRequest",
    "check_installed_advisories",
    "build_compliance_report_ref",
    "evaluate_credential_request",
    "evaluate_external_surface",
    "evaluate_lazy_dependency_request",
    "evaluate_sandbox_preflight",
    "evaluate_security_posture",
    "record_policy_kernel_decision",
    "scan_context_file",
]
