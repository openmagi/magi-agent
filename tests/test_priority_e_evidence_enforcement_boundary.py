from __future__ import annotations

import subprocess
import sys

from magi_agent.evidence.enforcement_boundary import (
    EvidenceEnforcementBoundary,
    EvidenceEnforcementConfig,
    EvidenceEnforcementRequest,
)
from magi_agent.evidence.types import EvidenceContract, EvidenceRecord


def _record(
    *,
    evidence_type: str = "TestRun",
    status: str = "ok",
    exit_code: int = 0,
    observed_at: int = 200,
) -> EvidenceRecord:
    return EvidenceRecord.model_validate(
        {
            "type": evidence_type,
            "status": status,
            "observedAt": observed_at,
            "source": {
                "kind": "tool_trace",
                "toolName": "Bash",
                "toolCallId": "call-tests",
            },
            "fields": {
                "command": "python -m pytest tests/test_unit.py",
                "exitCode": exit_code,
                "result": "passed" if exit_code == 0 else "failed",
            },
            "metadata": {
                "lastCodeMutation": 100,
                "rawPath": "/Users/kevin/private",
                "token": "ghp_evidenceSecret",
                "note": "raw transcript hidden_reasoning raw tool log",
            },
        }
    )


def _contract(on_missing: str = "block_final_answer") -> EvidenceContract:
    return EvidenceContract.model_validate(
        {
            "id": "coding-final-evidence",
            "triggers": ["beforeCommit"],
            "when": {"lastCodeMutation": 100},
            "requirements": [
                {
                    "type": "TestRun",
                    "after": "last_code_mutation",
                    "commandPattern": "^python -m pytest",
                    "exitCode": 0,
                }
            ],
            "onMissing": on_missing,
            "retryMessage": "Run tests and provide the evidence record.",
        }
    )


def test_evidence_enforcement_boundary_is_disabled_by_default() -> None:
    decision = EvidenceEnforcementBoundary(EvidenceEnforcementConfig()).evaluate(
        EvidenceEnforcementRequest(
            domain="coding",
            contract=_contract(),
            evidenceRecords=(_record(),),
        )
    )

    assert decision.status == "disabled"
    assert decision.action == "audit"
    assert decision.reason_codes == ("evidence_enforcement_disabled",)
    assert set(decision.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_evidence_enforcement_passes_with_local_fake_evaluation() -> None:
    decision = EvidenceEnforcementBoundary(
        EvidenceEnforcementConfig(enabled=True, localFakeEvaluationEnabled=True),
    ).evaluate(
        EvidenceEnforcementRequest(
            domain="coding",
            contract=_contract(),
            evidenceRecords=(_record(),),
        )
    )

    assert decision.status == "pass"
    assert decision.action == "pass"
    assert decision.verdict is not None
    assert decision.verdict.ok is True
    projection = decision.public_projection()
    assert projection["authorityFlags"]["finalAnswerBlocked"] is False
    assert "/Users/kevin" not in str(projection)
    assert "ghp_evidenceSecret" not in str(projection)
    assert "rawPath" not in str(projection)
    assert "raw transcript" not in str(projection)
    assert "hidden_reasoning" not in str(projection)
    assert "raw tool log" not in str(projection)


def test_evidence_enforcement_routes_missing_block_mode_to_repair_or_escalate() -> None:
    repair = EvidenceEnforcementBoundary(
        EvidenceEnforcementConfig(enabled=True, localFakeEvaluationEnabled=True),
    ).evaluate(
        EvidenceEnforcementRequest(
            domain="coding",
            contract=_contract(),
            evidenceRecords=(),
            repairAllowed=True,
        )
    )
    assert repair.status == "repair_required"
    assert repair.action == "repair"
    assert repair.reason_codes == ("evidence_repair_required",)

    escalate = EvidenceEnforcementBoundary(
        EvidenceEnforcementConfig(enabled=True, localFakeEvaluationEnabled=True),
    ).evaluate(
        EvidenceEnforcementRequest(
            domain="research",
            contract=_contract(),
            evidenceRecords=(),
            repairAllowed=False,
            escalationAllowed=True,
        )
    )
    assert escalate.status == "escalate_required"
    assert escalate.action == "escalate"


def test_evidence_enforcement_block_ready_is_intent_only_and_redacted() -> None:
    decision = EvidenceEnforcementBoundary(
        EvidenceEnforcementConfig(enabled=True, localFakeEvaluationEnabled=True),
    ).evaluate(
        EvidenceEnforcementRequest(
            domain="coding",
            contract=_contract(),
            evidenceRecords=(_record(exit_code=1),),
            repairAllowed=False,
            escalationAllowed=False,
            metadata={
                "rawToolResult": "/workspace/private output",
                "apiToken": "123456:ABC-secret-token",
                "note": "safe",
            },
        )
    )

    projection = decision.public_projection()
    assert decision.status == "block_ready_local_fake"
    assert decision.action == "block_intent"
    assert projection["authorityFlags"]["finalAnswerBlocked"] is False
    assert projection["authorityFlags"]["evidenceBlockEnabled"] is False
    assert "/workspace/private" not in str(projection)
    assert "123456:ABC-secret-token" not in str(projection)
    assert "ghp_evidenceSecret" not in str(projection)
    assert projection["diagnosticMetadata"]["note"] == "safe"


def test_evidence_diagnostic_metadata_cannot_forge_authority_flags() -> None:
    decision = EvidenceEnforcementBoundary(
        EvidenceEnforcementConfig(enabled=True, localFakeEvaluationEnabled=True),
    ).evaluate(
        EvidenceEnforcementRequest(
            domain="coding",
            contract=_contract(),
            evidenceRecords=(_record(),),
            metadata={
                "evidenceBlockEnabled": True,
                "finalAnswerBlockingEnabled": True,
                "routeAttached": True,
                "trusted": True,
                "authoritative": True,
                "note": "safe",
            },
        )
    )
    projection = decision.public_projection()
    diagnostic = str(projection["diagnosticMetadata"])

    assert decision.status == "pass"
    assert "evidenceBlockEnabled" not in diagnostic
    assert "finalAnswerBlockingEnabled" not in diagnostic
    assert "routeAttached" not in diagnostic
    assert "trusted" not in diagnostic
    assert "authoritative" not in diagnostic
    assert projection["diagnosticMetadata"]["note"] == "safe"
    assert projection["authorityFlags"]["evidenceBlockEnabled"] is False


def test_evidence_enforcement_boundary_has_no_live_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.evidence.enforcement_boundary")
forbidden = (
    "subprocess",
    "git",
    "google.adk.runners",
    "magi_agent.runtime.runner",
    "magi_agent.tools.dispatcher",
)
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise AssertionError(f"forbidden modules loaded: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
