"""Execution-authority kernel and live execution-integrity policy adapter.

The full universal broker remains an explicit governed-workflow API; the live
tool runtime consumes the shared journal, admission identities, read authority,
observations, and completion closure through :mod:`magi_agent.tools.execution_integrity`.
"""

SUPPORTED_SCHEMA_VERSIONS: tuple[str, ...] = (
    "magi.action_intent.v1",
    "magi.action_proposal.v1",
    "magi.action_receipt.v1",
    "magi.action_resolution.v1",
    "magi.completion_verdict.v1",
    "magi.dependency_health.v1",
    "magi.finalization_request.v1",
    "magi.recovery_decision.v1",
    "magi.response_claim_manifest.v1",
    "magi.task_contract.v1",
    "magi.user_decision_receipt.v1",
    "magi.user_decision_request.v1",
    "magi.workspace_commit_decision_request.v1",
    "magi.workspace_commit_recovery_claim_request.v1",
)
