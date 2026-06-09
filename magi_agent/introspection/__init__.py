from __future__ import annotations

from magi_agent.introspection.projection import (
    FileReadView,
    PhaseView,
    SessionEvidenceView,
    SessionScopeView,
    ToolCallView,
    VerdictView,
    project_session_evidence,
)
from magi_agent.introspection.tool import (
    INSPECT_SELF_EVIDENCE_DESCRIPTION,
    INSPECT_SELF_EVIDENCE_INPUT_SCHEMA,
    InspectSelfEvidenceToolHost,
    bind_inspect_self_evidence_handler,
    inspect_self_evidence,
    project_context_session_evidence,
)


__all__ = [
    "INSPECT_SELF_EVIDENCE_DESCRIPTION",
    "INSPECT_SELF_EVIDENCE_INPUT_SCHEMA",
    "FileReadView",
    "InspectSelfEvidenceToolHost",
    "PhaseView",
    "SessionEvidenceView",
    "SessionScopeView",
    "ToolCallView",
    "VerdictView",
    "bind_inspect_self_evidence_handler",
    "inspect_self_evidence",
    "project_context_session_evidence",
    "project_session_evidence",
]
