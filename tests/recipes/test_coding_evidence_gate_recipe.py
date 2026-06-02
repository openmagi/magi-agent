from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from magi_agent.evidence.types import EvidenceRecord, EvidenceSource
from magi_agent.recipes.coding_evidence_gate import (
    CodingEvidenceGate,
    CodingEvidenceGateConfig,
    CodingEvidenceGateRequest,
)


def _record(
    evidence_type: str,
    *,
    observed_at: int | float = 20,
    status: str = "ok",
    fields: dict[str, object] | None = None,
    preview: str | None = None,
) -> EvidenceRecord:
    return EvidenceRecord(
        type=evidence_type,
        status=status,
        observedAt=observed_at,
        source=EvidenceSource(kind="tool_trace", toolName=evidence_type),
        fields=fields or {},
        preview=preview,
        metadata={"publicSafeFields": ("command", "exitCode", "status")},
    )


def _request(
    *records: EvidenceRecord,
    completion_claimed: bool = True,
    claim_text: str = "Implementation is complete.",
    claim_ref: str = "claim:coding-1",
    last_code_mutation_at: int | float = 10,
    require_commit_checkpoint: bool = False,
) -> CodingEvidenceGateRequest:
    return CodingEvidenceGateRequest(
        evidenceRecords=records,
        completionClaimed=completion_claimed,
        claimText=claim_text,
        lastCodeMutationAt=last_code_mutation_at,
        requireCommitCheckpoint=require_commit_checkpoint,
        claimRef=claim_ref,
    )


def _enabled(enforcement: str = "audit") -> CodingEvidenceGate:
    return CodingEvidenceGate(
        CodingEvidenceGateConfig(
            enabled=True,
            localEvaluationEnabled=True,
            enforcement=enforcement,
        )
    )


def test_coding_evidence_gate_is_disabled_by_default() -> None:
    decision = CodingEvidenceGate().evaluate(
        _request(_record("GitDiff"), _record("TestRun")),
    )

    assert decision.status == "disabled"
    assert decision.reason_codes == ("coding_evidence_gate_disabled",)
    assert decision.public_projection()["authorityFlags"] == {
        "localEvaluationOnly": False,
        "localClaimBlocked": False,
        "finalAnswerBlocked": False,
        "userVisibleOutputAllowed": False,
        "trafficAttached": False,
        "runnerAttached": False,
        "liveToolAttached": False,
        "productionWriteAllowed": False,
    }


def test_completion_claim_requires_gitdiff_and_successful_testrun_after_mutation() -> None:
    decision = _enabled().evaluate(
        _request(
            _record("GitDiff", fields={"changedFiles": ("src/app.py",)}),
            _record("TestRun", fields={"command": "pytest", "exitCode": 0}),
        ),
    )

    assert decision.status == "passed"
    assert decision.reason_codes == ("coding_evidence_gate_passed",)
    assert decision.required_evidence_types == ("GitDiff", "TestRun")
    assert decision.matched_evidence_types == ("GitDiff", "TestRun")
    assert decision.missing_evidence_types == ()
    assert decision.public_projection()["authorityFlags"]["userVisibleOutputAllowed"] is False


def test_missing_evidence_is_audit_required_without_user_visible_blocking() -> None:
    decision = _enabled().evaluate(
        _request(_record("GitDiff", fields={"changedFiles": ("src/app.py",)})),
    )

    assert decision.status == "audit_required"
    assert decision.reason_codes == ("coding_evidence_missing",)
    assert decision.missing_evidence_types == ("TestRun",)
    projection = decision.public_projection()
    assert projection["authorityFlags"]["localClaimBlocked"] is False
    assert projection["authorityFlags"]["finalAnswerBlocked"] is False
    assert projection["authorityFlags"]["userVisibleOutputAllowed"] is False


def test_local_block_mode_blocks_only_local_claim_projection() -> None:
    decision = _enabled("local_block").evaluate(
        _request(_record("GitDiff", fields={"changedFiles": ("src/app.py",)})),
    )

    assert decision.status == "blocked_local"
    assert decision.reason_codes == ("coding_evidence_missing",)
    projection = decision.public_projection()
    assert projection["authorityFlags"]["localClaimBlocked"] is True
    assert projection["authorityFlags"]["finalAnswerBlocked"] is False
    assert projection["authorityFlags"]["userVisibleOutputAllowed"] is False


def test_optional_commit_checkpoint_requirement_is_recipe_configured() -> None:
    missing_checkpoint = _enabled().evaluate(
        _request(
            _record("GitDiff", fields={"changedFiles": ("src/app.py",)}),
            _record("TestRun", fields={"command": "pytest", "exitCode": 0}),
            require_commit_checkpoint=True,
        ),
    )
    with_checkpoint = _enabled().evaluate(
        _request(
            _record("GitDiff", fields={"changedFiles": ("src/app.py",)}),
            _record("TestRun", fields={"command": "pytest", "exitCode": 0}),
            _record("CommitCheckpoint", fields={"checkpointId": "commit-001"}),
            require_commit_checkpoint=True,
        ),
    )

    assert missing_checkpoint.status == "audit_required"
    assert missing_checkpoint.missing_evidence_types == ("CommitCheckpoint",)
    assert with_checkpoint.status == "passed"
    assert with_checkpoint.required_evidence_types == (
        "GitDiff",
        "TestRun",
        "CommitCheckpoint",
    )


def test_stale_or_failed_test_evidence_requires_repair() -> None:
    stale = _enabled().evaluate(
        _request(
            _record("GitDiff", observed_at=50, fields={"changedFiles": ("src/app.py",)}),
            _record("TestRun", observed_at=5, fields={"command": "pytest", "exitCode": 0}),
            last_code_mutation_at=10,
        ),
    )
    failed = _enabled().evaluate(
        _request(
            _record("GitDiff", fields={"changedFiles": ("src/app.py",)}),
            _record("TestRun", status="failed", fields={"command": "pytest", "exitCode": 1}),
        ),
    )

    assert stale.status == "repair_required"
    assert stale.failure_codes == ("EVIDENCE_CONTRACT_STALE",)
    assert failed.status == "repair_required"
    assert failed.failure_codes == ("EVIDENCE_CONTRACT_FIELD_MISMATCH",)


def test_non_completion_output_is_not_applicable() -> None:
    decision = _enabled("local_block").evaluate(
        _request(
            _record("GitDiff"),
            completion_claimed=False,
            claim_text="Here is a partial progress note.",
        ),
    )

    assert decision.status == "not_applicable"
    assert decision.reason_codes == ("no_completion_claim",)
    assert decision.required_evidence_types == ()


def test_public_projection_is_digest_only_and_clamps_forged_authority() -> None:
    decision = _enabled("local_block").evaluate(
        _request(
            _record(
                "GitDiff",
                fields={"changedFiles": ("/workspace/src/app.py",)},
                preview="diff -- /workspace/src/app.py token=sk-test-secret",
            ),
            claim_text="Done. token=sk-test-secret /workspace/src/app.py",
        ),
    ).model_copy(
        update={
            "authorityFlags": {
                "localEvaluationOnly": True,
                "localClaimBlocked": True,
                "finalAnswerBlocked": True,
                "userVisibleOutputAllowed": True,
                "trafficAttached": True,
                "runnerAttached": True,
                "liveToolAttached": True,
                "productionWriteAllowed": True,
            }
        }
    )

    projection = decision.public_projection()
    rendered = json.dumps(projection, sort_keys=True)

    assert "sk-test-secret" not in rendered
    assert "/workspace" not in rendered
    assert "Implementation is complete" not in rendered
    assert projection["claimDigest"].startswith("sha256:")
    assert projection["receiptRef"].startswith("coding-evidence-gate-receipt:")
    assert projection["authorityFlags"]["finalAnswerBlocked"] is False
    assert projection["authorityFlags"]["userVisibleOutputAllowed"] is False
    assert projection["authorityFlags"]["trafficAttached"] is False
    assert projection["authorityFlags"]["liveToolAttached"] is False


def test_claim_ref_rejects_token_shaped_values() -> None:
    for claim_ref in (
        "sk-proj-abcdef123456",
        "sk_live_abcdef123456",
        "sk_test_abcdef123456",
        "ghp_abcdef1234567890",
    ):
        try:
            _request(
                _record("GitDiff"),
                claim_ref=claim_ref,
            )
        except ValueError as exc:
            assert "claimRef" in str(exc)
        else:
            raise AssertionError(f"token-shaped claimRef was accepted: {claim_ref}")


def test_forged_digest_and_receipt_refs_are_redacted_in_projection() -> None:
    decision = _enabled().evaluate(
        _request(
            _record("GitDiff", fields={"changedFiles": ("src/app.py",)}),
            _record("TestRun", fields={"command": "pytest", "exitCode": 0}),
        ),
    ).model_copy(
        update={
            "claimDigest": "/workspace/sk-test-secret",
            "receiptRef": "coding-evidence-gate-receipt:/workspace/sk-test-secret",
        }
    )

    projection = decision.public_projection()

    assert projection["claimDigest"] == "sha256:" + ("0" * 64)
    assert projection["receiptRef"] == "redacted_ref"


def test_coding_evidence_gate_import_boundary_has_no_live_runtime_surfaces() -> None:
    source = (
        Path(__file__).parents[2]
        / "magi_agent"
        / "recipes"
        / "coding_evidence_gate.py"
    ).read_text(encoding="utf-8")
    for token in (
        "subprocess",
        "ToolHost",
        "ToolDispatcher",
        "google.adk.runners",
        "FastAPI",
        "git ",
        "pytest ",
    ):
        assert token not in source

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.recipes.coding_evidence_gate")
forbidden = (
    "google.adk.runners",
    "google.adk.sessions",
    "google.adk.models",
    "magi_agent.adk_bridge",
    "magi_agent.runtime.adk_turn_runner",
    "magi_agent.transport.chat",
    "magi_agent.tools.dispatcher",
)
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise AssertionError(f"coding evidence gate import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
