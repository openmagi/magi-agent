"""Tests for PR7: Governed Coding Final Projection.

Verifies that final coding responses only include verified claims backed by
evidence (mutation receipts, diff evidence, test runs). Unverified "done" /
"fixed" / "all tests pass" claims are downgraded or blocked.
"""
from __future__ import annotations

import hashlib

import pytest

from magi_agent.coding.final_projection import (
    CodingFinalProjection,
    CodingFinalProjectionResult,
    EvidenceGap,
    FileChangeRecord,
    RollbackStatus,
    TestRunRecord,
    build_final_projection,
    downgrade_unsupported_claims,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(content: str) -> str:
    return f"sha256:{hashlib.sha256(content.encode()).hexdigest()}"


def _full_evidence_projection() -> CodingFinalProjection:
    """All evidence present, tests pass, rollback verified."""
    return CodingFinalProjection.model_validate({
        "changedFiles": [
            {
                "fileDigest": _sha256("src/foo.py"),
                "operation": "modified",
                "diffEvidenceRef": "diff:src-foo-py-r2",
            },
        ],
        "testsRun": [
            {
                "testSuiteRef": "test:unit-suite",
                "status": "pass",
                "evidenceRef": "test:unit-suite-receipt",
            },
        ],
        "evidenceGaps": [],
        "rollbackStatus": {
            "gate2ReceiptRef": "receipt:" + _sha256("gate2")[-71:],
            "verified": True,
        },
        "nextAction": None,
        "defaultOff": True,
        "productionWorkspaceMutationAllowed": False,
    })


def _missing_tests_projection() -> CodingFinalProjection:
    """Files changed but no tests run."""
    return CodingFinalProjection.model_validate({
        "changedFiles": [
            {
                "fileDigest": _sha256("src/bar.py"),
                "operation": "created",
                "diffEvidenceRef": "diff:src-bar-py-r1",
            },
        ],
        "testsRun": [],
        "evidenceGaps": [
            {
                "gapType": "missing_test_evidence",
                "description": "No test run evidence for changed files",
            },
        ],
        "rollbackStatus": {
            "gate2ReceiptRef": "receipt:" + _sha256("gate2")[-71:],
            "verified": True,
        },
        "nextAction": "Run tests before claiming completion",
        "defaultOff": True,
        "productionWorkspaceMutationAllowed": False,
    })


def _failed_tests_projection() -> CodingFinalProjection:
    """Tests were run but some failed."""
    return CodingFinalProjection.model_validate({
        "changedFiles": [
            {
                "fileDigest": _sha256("src/baz.py"),
                "operation": "modified",
                "diffEvidenceRef": "diff:src-baz-py-r3",
            },
        ],
        "testsRun": [
            {
                "testSuiteRef": "test:unit-suite",
                "status": "failed",
                "evidenceRef": "test:unit-suite-failed-receipt",
            },
        ],
        "evidenceGaps": [
            {
                "gapType": "failed_test_evidence",
                "description": "Test suite reported failures",
            },
        ],
        "rollbackStatus": {
            "gate2ReceiptRef": "receipt:" + _sha256("gate2")[-71:],
            "verified": True,
        },
        "nextAction": "Fix failing tests before claiming completion",
        "defaultOff": True,
        "productionWorkspaceMutationAllowed": False,
    })


def _partial_completion_projection() -> CodingFinalProjection:
    """Some files changed, tests pass, but evidence gaps remain."""
    return CodingFinalProjection.model_validate({
        "changedFiles": [
            {
                "fileDigest": _sha256("src/partial.py"),
                "operation": "modified",
                "diffEvidenceRef": "diff:src-partial-py-r2",
            },
        ],
        "testsRun": [
            {
                "testSuiteRef": "test:unit-suite",
                "status": "pass",
                "evidenceRef": "test:unit-suite-receipt",
            },
        ],
        "evidenceGaps": [
            {
                "gapType": "incomplete_scope",
                "description": "Only 1 of 3 planned files were modified",
            },
        ],
        "rollbackStatus": {
            "gate2ReceiptRef": "receipt:" + _sha256("gate2")[-71:],
            "verified": True,
        },
        "nextAction": "Complete remaining file modifications",
        "defaultOff": True,
        "productionWorkspaceMutationAllowed": False,
    })


def _rollback_missing_projection() -> CodingFinalProjection:
    """No gate2 receipt for rollback verification."""
    return CodingFinalProjection.model_validate({
        "changedFiles": [
            {
                "fileDigest": _sha256("src/risky.py"),
                "operation": "modified",
                "diffEvidenceRef": "diff:src-risky-py-r1",
            },
        ],
        "testsRun": [
            {
                "testSuiteRef": "test:unit-suite",
                "status": "pass",
                "evidenceRef": "test:unit-suite-receipt",
            },
        ],
        "evidenceGaps": [
            {
                "gapType": "missing_rollback_receipt",
                "description": "No Gate 2 receipt for rollback verification",
            },
        ],
        "rollbackStatus": {
            "gate2ReceiptRef": None,
            "verified": False,
        },
        "nextAction": "Obtain Gate 2 rollback receipt",
        "defaultOff": True,
        "productionWorkspaceMutationAllowed": False,
    })


# ---------------------------------------------------------------------------
# Test: Verified success — all evidence present
# ---------------------------------------------------------------------------

class TestVerifiedSuccess:
    def test_full_evidence_builds_successfully(self) -> None:
        proj = _full_evidence_projection()
        assert len(proj.changed_files) == 1
        assert proj.changed_files[0].operation == "modified"
        assert len(proj.tests_run) == 1
        assert proj.tests_run[0].status == "pass"
        assert len(proj.evidence_gaps) == 0
        assert proj.rollback_status.verified is True
        assert proj.next_action is None

    def test_full_evidence_result_is_complete(self) -> None:
        proj = _full_evidence_projection()
        result = build_final_projection(proj)
        assert result.status == "complete"
        assert result.has_evidence_gaps is False
        assert result.projection == proj

    def test_full_evidence_public_projection_is_digest_safe(self) -> None:
        proj = _full_evidence_projection()
        result = build_final_projection(proj)
        public = result.public_projection()
        # Must not contain raw file paths
        assert "/Users" not in str(public)
        assert "/home" not in str(public)
        assert "/data/bots" not in str(public)
        # Must contain digests
        assert any("sha256:" in str(v) for v in public.values())

    def test_production_workspace_mutation_always_false(self) -> None:
        proj = _full_evidence_projection()
        assert proj.production_workspace_mutation_allowed is False

    def test_default_off_always_true(self) -> None:
        proj = _full_evidence_projection()
        assert proj.default_off is True


# ---------------------------------------------------------------------------
# Test: Missing tests
# ---------------------------------------------------------------------------

class TestMissingTests:
    def test_missing_tests_has_evidence_gap(self) -> None:
        proj = _missing_tests_projection()
        assert len(proj.evidence_gaps) == 1
        assert proj.evidence_gaps[0].gap_type == "missing_test_evidence"

    def test_missing_tests_result_is_incomplete(self) -> None:
        proj = _missing_tests_projection()
        result = build_final_projection(proj)
        assert result.status == "incomplete"
        assert result.has_evidence_gaps is True

    def test_missing_tests_next_action_set(self) -> None:
        proj = _missing_tests_projection()
        assert proj.next_action is not None
        assert "test" in proj.next_action.lower()


# ---------------------------------------------------------------------------
# Test: Failed tests
# ---------------------------------------------------------------------------

class TestFailedTests:
    def test_failed_tests_has_evidence_gap(self) -> None:
        proj = _failed_tests_projection()
        assert len(proj.evidence_gaps) == 1
        assert proj.evidence_gaps[0].gap_type == "failed_test_evidence"

    def test_failed_tests_result_is_incomplete(self) -> None:
        proj = _failed_tests_projection()
        result = build_final_projection(proj)
        assert result.status == "incomplete"
        assert result.has_evidence_gaps is True

    def test_failed_tests_next_action_set(self) -> None:
        proj = _failed_tests_projection()
        assert proj.next_action is not None
        assert "fix" in proj.next_action.lower() or "test" in proj.next_action.lower()


# ---------------------------------------------------------------------------
# Test: Partial completion
# ---------------------------------------------------------------------------

class TestPartialCompletion:
    def test_partial_completion_has_evidence_gap(self) -> None:
        proj = _partial_completion_projection()
        assert len(proj.evidence_gaps) == 1
        assert proj.evidence_gaps[0].gap_type == "incomplete_scope"

    def test_partial_completion_result_is_incomplete(self) -> None:
        proj = _partial_completion_projection()
        result = build_final_projection(proj)
        assert result.status == "incomplete"
        assert result.has_evidence_gaps is True


# ---------------------------------------------------------------------------
# Test: Rollback-missing failure
# ---------------------------------------------------------------------------

class TestRollbackMissing:
    def test_rollback_missing_has_evidence_gap(self) -> None:
        proj = _rollback_missing_projection()
        assert len(proj.evidence_gaps) == 1
        assert proj.evidence_gaps[0].gap_type == "missing_rollback_receipt"

    def test_rollback_missing_not_verified(self) -> None:
        proj = _rollback_missing_projection()
        assert proj.rollback_status.verified is False
        assert proj.rollback_status.gate2_receipt_ref is None

    def test_rollback_missing_result_is_incomplete(self) -> None:
        proj = _rollback_missing_projection()
        result = build_final_projection(proj)
        assert result.status == "incomplete"
        assert result.has_evidence_gaps is True


# ---------------------------------------------------------------------------
# Test: Unsupported claim downgrade
# ---------------------------------------------------------------------------

class TestUnsupportedClaimDowngrade:
    @pytest.mark.parametrize("claim", [
        "All tests pass",
        "Everything is done",
        "I fixed the bug",
        "The implementation is complete",
        "All changes have been applied successfully",
        "Tests are passing now",
        "Done! All good.",
        "Fixed and verified",
    ])
    def test_unsupported_claims_are_downgraded(self, claim: str) -> None:
        result = downgrade_unsupported_claims(claim)
        assert result != claim, f"Claim should have been downgraded: {claim!r}"
        assert "[unverified:" in result.lower() or "[downgraded:" in result.lower()

    @pytest.mark.parametrize("safe_text", [
        "Modified src/foo.py: added error handling",
        "Test suite ran with 3 passing, 0 failing",
        "Changed 2 files with diff evidence",
        "Rollback receipt verified via Gate 2",
    ])
    def test_safe_text_not_downgraded(self, safe_text: str) -> None:
        result = downgrade_unsupported_claims(safe_text)
        assert result == safe_text


# ---------------------------------------------------------------------------
# Test: Model invariants
# ---------------------------------------------------------------------------

class TestModelInvariants:
    def test_production_workspace_mutation_cannot_be_true(self) -> None:
        with pytest.raises(Exception):
            CodingFinalProjection.model_validate({
                "changedFiles": [],
                "testsRun": [],
                "evidenceGaps": [],
                "rollbackStatus": {"gate2ReceiptRef": None, "verified": False},
                "nextAction": None,
                "defaultOff": True,
                "productionWorkspaceMutationAllowed": True,
            })

    def test_default_off_cannot_be_false(self) -> None:
        with pytest.raises(Exception):
            CodingFinalProjection.model_validate({
                "changedFiles": [],
                "testsRun": [],
                "evidenceGaps": [],
                "rollbackStatus": {"gate2ReceiptRef": None, "verified": False},
                "nextAction": None,
                "defaultOff": False,
                "productionWorkspaceMutationAllowed": False,
            })

    def test_file_digest_must_be_sha256(self) -> None:
        with pytest.raises(Exception):
            FileChangeRecord.model_validate({
                "fileDigest": "not-a-hash",
                "operation": "modified",
                "diffEvidenceRef": "diff:some-ref",
            })

    def test_operation_must_be_valid(self) -> None:
        with pytest.raises(Exception):
            FileChangeRecord.model_validate({
                "fileDigest": _sha256("test"),
                "operation": "exploded",
                "diffEvidenceRef": "diff:some-ref",
            })

    def test_test_status_must_be_valid(self) -> None:
        with pytest.raises(Exception):
            TestRunRecord.model_validate({
                "testSuiteRef": "test:suite",
                "status": "maybe",
                "evidenceRef": "test:receipt",
            })

    def test_gap_type_must_be_valid(self) -> None:
        with pytest.raises(Exception):
            EvidenceGap.model_validate({
                "gapType": "invalid_gap_type",
                "description": "something",
            })

    @pytest.mark.parametrize("description", [
        "Authorization: Bearer abcdefgh123456 leaked from a tool result",
        "Raw source snapshot included private evidence",
        "Callback URL had ?code=abcd&session=secret-state",
        "Fetch https://example.test/api/token before retrying",
        "Use access token abcdefgh123456 to continue",
        "Callback code abcdefgh123456 appeared in logs",
        "Cookie session=abcdefgh123456 appeared in output",
        "Windows path C:\\Users\\kevin\\secret.txt appeared in output",
        "refresh token abcdefgh123456 appeared in output",
        "id token abcdefgh123456 appeared in output",
        "token abcdefgh123456 appeared in output",
        "session=abcdefgh123456 appeared in output",
        "access_token=abc appeared in output",
        "access_token=... appeared in output",
        "refresh_token=... appeared in output",
        "id_token=... appeared in output",
        "SSH key path /root/.ssh/id_rsa appeared in output",
        "refresh_token=abcdefgh123456 appeared in output",
        "id_token=abcdefgh123456 appeared in output",
        "token=abcdefgh123456 appeared in output",
        "cookie=abcdefgh123456 appeared in output",
        "raw_tool_output appeared in output",
        "raw-tool-output appeared in output",
        "private_source appeared in output",
        "chain-of-thought appeared in output",
        "api key abcdefgh123456 appeared in output",
        "api key abc123 appeared in output",
        "api key is abcdefgh appeared in output",
        "password abcdefgh123456 appeared in output",
        "password is hunter2 appeared in output",
        "password is swordfish appeared in output",
        "refresh_token abcdefgh123456 appeared in output",
        "raw_source_snapshot appeared in output",
        "child evidence appeared in output",
    ])
    def test_evidence_gap_description_rejects_unsafe_public_text(
        self,
        description: str,
    ) -> None:
        with pytest.raises(Exception, match="unsafe public text"):
            EvidenceGap.model_validate({
                "gapType": "missing_test_evidence",
                "description": description,
            })

    @pytest.mark.parametrize("next_action", [
        "Paste Cookie: session=abcdef into the rerun",
        "Review raw tool output before claiming completion",
        "Open /oauth/callback?token=secret-state before retrying",
        "Inspect /api/token before retrying",
        "Use access token abcdefgh123456 to continue",
        "Copy callback code abcdefgh123456 into the next request",
        "Open C:\\Users\\kevin\\secret.txt before retrying",
        "Use refresh token abcdefgh123456 to continue",
        "Use id token abcdefgh123456 to continue",
        "Paste token abcdefgh123456 into the request",
        "Reuse session=abcdefgh123456 before retrying",
        "Reuse access_token=abc before retrying",
        "Reuse access_token=... before retrying",
        "Reuse refresh_token=... before retrying",
        "Reuse id_token=... before retrying",
        "Open /root/.ssh/id_rsa before retrying",
        "Reuse refresh_token=abcdefgh123456 before retrying",
        "Reuse id_token=abcdefgh123456 before retrying",
        "Reuse token=abcdefgh123456 before retrying",
        "Reuse cookie=abcdefgh123456 before retrying",
        "Review raw_tool_output before retrying",
        "Review raw-tool-output before retrying",
        "Review private_source before retrying",
        "Review chain-of-thought before retrying",
        "Paste api key abcdefgh123456 before retrying",
        "Paste api key abc123 before retrying",
        "Paste api key is abcdefgh before retrying",
        "Paste password abcdefgh123456 before retrying",
        "Paste password is hunter2 before retrying",
        "Paste password is swordfish before retrying",
        "Paste refresh_token abcdefgh123456 before retrying",
        "Review raw_source_snapshot before retrying",
        "Review child evidence before retrying",
    ])
    def test_next_action_rejects_unsafe_public_text(self, next_action: str) -> None:
        with pytest.raises(Exception, match="unsafe public text"):
            CodingFinalProjection.model_validate({
                "changedFiles": [],
                "testsRun": [],
                "evidenceGaps": [
                    {
                        "gapType": "missing_test_evidence",
                        "description": "Missing test evidence",
                    },
                ],
                "rollbackStatus": {"gate2ReceiptRef": None, "verified": False},
                "nextAction": next_action,
                "defaultOff": True,
                "productionWorkspaceMutationAllowed": False,
            })

    def test_next_action_allows_benign_session_wording(self) -> None:
        projection = CodingFinalProjection.model_validate({
            "changedFiles": [],
            "testsRun": [],
            "evidenceGaps": [
                {
                    "gapType": "missing_test_evidence",
                    "description": "Missing test evidence",
                },
            ],
            "rollbackStatus": {"gate2ReceiptRef": None, "verified": False},
            "nextAction": "Restore session persistence metadata after tests pass",
            "defaultOff": True,
            "productionWorkspaceMutationAllowed": False,
        })

        assert projection.next_action == "Restore session persistence metadata after tests pass"

    def test_next_action_allows_benign_source_format_wording(self) -> None:
        projection = CodingFinalProjection.model_validate({
            "changedFiles": [],
            "testsRun": [],
            "evidenceGaps": [
                {
                    "gapType": "missing_test_evidence",
                    "description": "Missing test evidence",
                },
            ],
            "rollbackStatus": {"gate2ReceiptRef": None, "verified": False},
            "nextAction": "Convert source HTML fixtures into digest metadata",
            "defaultOff": True,
            "productionWorkspaceMutationAllowed": False,
        })

        assert projection.next_action == "Convert source HTML fixtures into digest metadata"

    def test_next_action_allows_benign_public_session_route_wording(self) -> None:
        projection = CodingFinalProjection.model_validate({
            "changedFiles": [],
            "testsRun": [],
            "evidenceGaps": [
                {
                    "gapType": "missing_test_evidence",
                    "description": "Missing test evidence",
                },
            ],
            "rollbackStatus": {"gate2ReceiptRef": None, "verified": False},
            "nextAction": "Check /api/session health route after the fix",
            "defaultOff": True,
            "productionWorkspaceMutationAllowed": False,
        })

        assert projection.next_action == "Check /api/session health route after the fix"


# ---------------------------------------------------------------------------
# Test: Public projection digest safety
# ---------------------------------------------------------------------------

class TestPublicProjectionDigestSafety:
    def test_public_projection_contains_only_safe_fields(self) -> None:
        proj = _full_evidence_projection()
        result = build_final_projection(proj)
        public = result.public_projection()
        serialized = str(public)
        # No raw paths
        assert "/Users/" not in serialized
        assert "/home/" not in serialized
        assert "/data/bots/" not in serialized
        assert "/workspace/" not in serialized
        # No auth tokens
        assert "Bearer " not in serialized
        assert "sk-" not in serialized

    def test_public_projection_keys_are_camel_case(self) -> None:
        proj = _full_evidence_projection()
        result = build_final_projection(proj)
        public = result.public_projection()
        for key in public:
            assert "_" not in key, f"Public key {key!r} should be camelCase"

    def test_public_projection_is_readable(self) -> None:
        proj = _full_evidence_projection()
        result = build_final_projection(proj)
        public = result.public_projection()
        assert "status" in public
        assert "changedFileCount" in public
        assert "testRunCount" in public
        assert "evidenceGapCount" in public
        assert "rollbackVerified" in public


# ---------------------------------------------------------------------------
# Test: SSE sanitizer blocks raw/private content
# ---------------------------------------------------------------------------

class TestSSESanitizerIntegration:
    def test_sse_sanitizer_strips_raw_paths(self) -> None:
        from magi_agent.transport.sse import _sanitize_coding_final_projection_event

        event: dict[str, object] = {
            "type": "coding_final_projection",
            "status": "complete",
            "changedFileCount": 1,
            "testRunCount": 1,
            "evidenceGapCount": 0,
            "rollbackVerified": True,
            "nextAction": None,
            "defaultOff": True,
            "productionWorkspaceMutationAllowed": False,
            "changedFiles": [
                {
                    "fileDigest": _sha256("content"),
                    "operation": "modified",
                    "diffEvidenceRef": "diff:src-foo-r1",
                },
            ],
            "testsRun": [
                {
                    "testSuiteRef": "test:unit",
                    "status": "pass",
                    "evidenceRef": "test:receipt-1",
                },
            ],
            "evidenceGaps": [],
        }
        result = _sanitize_coding_final_projection_event(event)
        assert result is not None
        assert result["type"] == "coding_final_projection"
        assert result["status"] == "complete"
        serialized = str(result)
        assert "/Users/" not in serialized
        assert "/home/" not in serialized

    def test_sse_sanitizer_redacts_private_next_action(self) -> None:
        from magi_agent.transport.sse import _sanitize_coding_final_projection_event

        event: dict[str, object] = {
            "type": "coding_final_projection",
            "status": "incomplete",
            "nextAction": "Check raw prompt at /Users/kevin/secret.txt",
            "defaultOff": True,
            "productionWorkspaceMutationAllowed": False,
        }
        result = _sanitize_coding_final_projection_event(event)
        assert result is not None
        # Path should be redacted
        assert "/Users/kevin" not in str(result.get("nextAction", ""))

    def test_sse_sanitizer_rejects_unknown_status(self) -> None:
        from magi_agent.transport.sse import _sanitize_coding_final_projection_event

        event: dict[str, object] = {
            "type": "coding_final_projection",
            "status": "hacked",
        }
        result = _sanitize_coding_final_projection_event(event)
        assert result is not None
        assert "status" not in result  # invalid status stripped

    def test_sse_sanitizer_drops_invalid_digest(self) -> None:
        from magi_agent.transport.sse import _sanitize_coding_final_projection_event

        event: dict[str, object] = {
            "type": "coding_final_projection",
            "changedFiles": [
                {
                    "fileDigest": "not-a-real-hash",
                    "operation": "modified",
                    "diffEvidenceRef": "diff:r1",
                },
            ],
        }
        result = _sanitize_coding_final_projection_event(event)
        assert result is not None
        files = result.get("changedFiles", [])
        # Invalid digest should not be passed through
        if files:
            assert "fileDigest" not in files[0] or files[0].get("fileDigest", "").startswith("sha256:")
