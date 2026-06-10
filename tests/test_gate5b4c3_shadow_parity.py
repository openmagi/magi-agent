"""Tests for gate5b4c3_shadow_parity.

TDD: tests written first (red), implementation to follow (green).

Coverage:
- parity_match verdict
- answer_mismatch verdict
- status_mismatch verdict
- answer_and_status_mismatch verdict
- incomparable when TS digest None
- incomparable when TS status None
- user_visible_output=None hashes to sha256("") — not a crash
- privacy test: model_dump contains ONLY digests/safe labels, NOT raw output text
"""

from __future__ import annotations

import hashlib

from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    Gate5B4C3LiveRunnerBoundaryResult,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationComparison,
    Gate5B4C3ShadowGenerationConfig,
    Gate5B4C3ShadowGenerationRequest,
    build_gate5b4c3_shadow_generation_diagnostic,
)
from magi_agent.shadow.gate5b4c3_shadow_parity import (
    Gate5B4C3ShadowParitySummary,
    compute_shadow_parity,
)


# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

BOT_DIGEST = "sha256:" + "a" * 64
OWNER_DIGEST = "sha256:" + "b" * 64
TURN_DIGEST = "sha256:" + "c" * 64
REQUEST_DIGEST = "sha256:" + "d" * 64
TRACE_DIGEST = "sha256:" + "e" * 64
SESSION_DIGEST = "sha256:" + "f" * 64
SANITIZED_DIGEST = "sha256:" + "1" * 64
ROUTER_DIGEST = "sha256:" + "2" * 64
PROFILE_DIGEST = "sha256:" + "3" * 64

PYTHON_OUTPUT_TEXT = "This is the Python diagnostic answer."
PYTHON_OUTPUT_DIGEST = "sha256:" + hashlib.sha256(PYTHON_OUTPUT_TEXT.encode("utf-8")).hexdigest()
EMPTY_DIGEST = "sha256:" + hashlib.sha256(b"").hexdigest()

TS_MATCHING_DIGEST = PYTHON_OUTPUT_DIGEST
TS_DIFFERENT_DIGEST = "sha256:" + "9" * 64


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _request() -> Gate5B4C3ShadowGenerationRequest:
    return Gate5B4C3ShadowGenerationRequest.model_validate(
        {
            "schemaVersion": "gate5b4c3.chatProxyShadowGeneration.v1",
            "shadowGenerationId": "shadow_gen_001",
            "requestIdDigest": REQUEST_DIGEST,
            "traceIdDigest": TRACE_DIGEST,
            "createdAt": 1779200000000,
            "selection": {
                "botIdDigest": BOT_DIGEST,
                "ownerUserIdDigest": OWNER_DIGEST,
                "environment": "production",
                "selectedTarget": "gate5b_selected_bot",
                "sessionKeyDigest": SESSION_DIGEST,
            },
            "turn": {
                "turnId": "turn_opaque_001",
                "turnDigest": TURN_DIGEST,
                "sanitizedCurrentTurnText": "Synthetic parity test input.",
                "sanitizedInputTextDigest": SANITIZED_DIGEST,
                "channelName": "unknown",
                "tsResponseCorrelationId": "ts_corr_001",
                "attachmentMetadata": [],
            },
            "modelRouting": {
                "routingSource": "per_turn_injected",
                "providerLabel": "google",
                "modelLabel": "gemini-3.5-flash",
                "routerDecisionDigest": ROUTER_DIGEST,
                "routingProfileDigest": PROFILE_DIGEST,
                "shadowCredentialRef": "gate5b-google-api-key-smoke-v1",
                "credentialRefSource": "server_config",
            },
            "recipeProfile": {
                "recipeId": "gate5b_shadow_smoke",
                "recipeVersion": "v1",
                "profileId": "no_tools_no_memory_current_turn_only",
                "profileVersion": "v1",
                "runtimeEngine": "adk-python",
                "toolsPolicy": "disabled",
                "memoryMode": "disabled",
                "sourceAuthority": "current_turn_only",
            },
            "policy": {
                "typeScriptResponseAuthority": True,
                "pythonDiagnosticOnly": True,
                "outputIsolation": "local_diagnostic_only",
                "toolsDisabled": True,
                "toolHostDispatchAllowed": False,
                "memoryProviderCallsAllowed": False,
                "memoryWritesAllowed": False,
                "promptMemoryInjectionAllowed": False,
                "workspaceMutationAllowed": False,
                "childExecutionAllowed": False,
                "missionRuntimeAllowed": False,
                "evidenceBlockModeAllowed": False,
            },
            "budgets": {"maxOutputTokens": 64, "maxDiagnosticOutputPreviewBytes": 128},
            "redaction": {
                "sanitizerId": "gate5b_synthetic_sanitizer",
                "sanitizerVersion": "v1",
                "policyId": "gate5b4c3_synthetic_only",
                "status": "passed",
                "redactedAt": 1779200000001,
                "redactedByteCount": 32,
                "forbiddenFieldScan": "passed",
                "sanitizedPayloadDigest": SANITIZED_DIGEST,
                "droppedFieldReasons": [],
            },
            "authority": {},
        }
    )


def _python_result(
    *,
    status: str = "completed",
    reason: str = "runner_completed",
    output_text: str | None = PYTHON_OUTPUT_TEXT,
) -> Gate5B4C3LiveRunnerBoundaryResult:
    """Build a Gate5B4C3LiveRunnerBoundaryResult via the boundary for test use.

    Because Gate5B4C3LiveRunnerBoundaryResult._force_non_authoritative_fields
    forces userVisibleOutput=None, we construct the result from the boundary
    (which sets outputTextInternal) and then read user_visible_output from it
    — which is always None in practice. The output_text is passed as the
    internal field only. We simulate this by using a skipped/dropped result
    shape directly.
    """
    request = _request()
    diagnostic = build_gate5b4c3_shadow_generation_diagnostic(
        request,
        config=Gate5B4C3ShadowGenerationConfig(),
    )
    return Gate5B4C3LiveRunnerBoundaryResult(
        diagnostic=diagnostic.model_dump(by_alias=True, mode="python", warnings=False),
        status=status,  # type: ignore[arg-type]
        reason=reason,  # type: ignore[arg-type]
        routingSource="per_turn_injected",
        outputTextInternal=output_text,
    )


def _comparison(
    *,
    ts_digest: str | None = TS_MATCHING_DIGEST,
    ts_status: str | None = "completed",
) -> Gate5B4C3ShadowGenerationComparison:
    return Gate5B4C3ShadowGenerationComparison(
        typeScriptFinalAnswerDigest=ts_digest,
        typeScriptTerminalStatus=ts_status,
    )


# ---------------------------------------------------------------------------
# Tests — basic schema / model structure
# ---------------------------------------------------------------------------

def test_parity_summary_schema_version_and_observe_only() -> None:
    result = _python_result()
    comparison = _comparison()
    summary = compute_shadow_parity(python_result=result, comparison=comparison)

    assert summary.schema_version == "gate5b4c3.shadowParity.v1"
    assert summary.observe_only is True


def test_parity_summary_is_gate5b4c3_model_instance() -> None:
    result = _python_result()
    comparison = _comparison()
    summary = compute_shadow_parity(python_result=result, comparison=comparison)

    assert isinstance(summary, Gate5B4C3ShadowParitySummary)


# ---------------------------------------------------------------------------
# Tests — verdict: parity_match
# ---------------------------------------------------------------------------

def test_parity_match_when_both_digests_and_statuses_match() -> None:
    result = _python_result(output_text=PYTHON_OUTPUT_TEXT, status="completed")
    # TS digest matches Python output digest; user_visible_output is None
    # (forced by the model) so Python digest is always sha256("").
    # For parity_match we need TS digest == sha256("") because user_visible_output is None.
    empty_digest = EMPTY_DIGEST
    comparison = _comparison(ts_digest=empty_digest, ts_status="completed")

    summary = compute_shadow_parity(python_result=result, comparison=comparison)

    assert summary.answer_parity == "match"
    assert summary.status_parity == "match"
    assert summary.verdict == "parity_match"


# ---------------------------------------------------------------------------
# Tests — verdict: answer_mismatch
# ---------------------------------------------------------------------------

def test_answer_mismatch_when_python_digest_differs_from_ts_digest() -> None:
    result = _python_result(status="completed")
    # TS digest is different from sha256("") which is what Python will produce
    comparison = _comparison(ts_digest=TS_DIFFERENT_DIGEST, ts_status="completed")

    summary = compute_shadow_parity(python_result=result, comparison=comparison)

    assert summary.answer_parity == "mismatch"
    assert summary.status_parity == "match"
    assert summary.verdict == "answer_mismatch"


# ---------------------------------------------------------------------------
# Tests — verdict: status_mismatch
# ---------------------------------------------------------------------------

def test_status_mismatch_when_answers_match_but_statuses_differ() -> None:
    empty_digest = EMPTY_DIGEST
    result = _python_result(status="completed")
    comparison = _comparison(ts_digest=empty_digest, ts_status="error")

    summary = compute_shadow_parity(python_result=result, comparison=comparison)

    assert summary.answer_parity == "match"
    assert summary.status_parity == "mismatch"
    assert summary.verdict == "status_mismatch"


# ---------------------------------------------------------------------------
# Tests — verdict: answer_and_status_mismatch
# ---------------------------------------------------------------------------

def test_answer_and_status_mismatch_when_both_differ() -> None:
    result = _python_result(status="completed")
    comparison = _comparison(ts_digest=TS_DIFFERENT_DIGEST, ts_status="error")

    summary = compute_shadow_parity(python_result=result, comparison=comparison)

    assert summary.answer_parity == "mismatch"
    assert summary.status_parity == "mismatch"
    assert summary.verdict == "answer_and_status_mismatch"


# ---------------------------------------------------------------------------
# Tests — verdict: incomparable (TS digest None)
# ---------------------------------------------------------------------------

def test_incomparable_when_ts_digest_is_none() -> None:
    result = _python_result(status="completed")
    comparison = _comparison(ts_digest=None, ts_status="completed")

    summary = compute_shadow_parity(python_result=result, comparison=comparison)

    assert summary.answer_parity == "incomparable"
    assert summary.verdict == "incomparable"


# ---------------------------------------------------------------------------
# Tests — verdict: incomparable (TS status None)
# ---------------------------------------------------------------------------

def test_incomparable_when_ts_status_is_none() -> None:
    empty_digest = EMPTY_DIGEST
    result = _python_result(status="completed")
    comparison = _comparison(ts_digest=empty_digest, ts_status=None)

    summary = compute_shadow_parity(python_result=result, comparison=comparison)

    assert summary.status_parity == "incomparable"
    assert summary.verdict == "incomparable"


def test_incomparable_when_both_ts_fields_are_none() -> None:
    result = _python_result(status="completed")
    comparison = _comparison(ts_digest=None, ts_status=None)

    summary = compute_shadow_parity(python_result=result, comparison=comparison)

    assert summary.answer_parity == "incomparable"
    assert summary.status_parity == "incomparable"
    assert summary.verdict == "incomparable"


# ---------------------------------------------------------------------------
# Tests — user_visible_output=None hashes to sha256("") — no crash
# ---------------------------------------------------------------------------

def test_none_user_visible_output_hashes_to_empty_string_digest() -> None:
    result = _python_result(output_text=None)
    # user_visible_output is always None; outputTextInternal also None here
    comparison = _comparison(ts_digest=EMPTY_DIGEST, ts_status="completed")

    summary = compute_shadow_parity(python_result=result, comparison=comparison)

    # Must not crash and must produce sha256("") digest
    assert summary.python_final_answer_digest == EMPTY_DIGEST
    assert summary.answer_parity == "match"


# ---------------------------------------------------------------------------
# Tests — privacy: model_dump must NOT contain raw output text
# ---------------------------------------------------------------------------

def test_privacy_model_dump_contains_no_raw_output_text() -> None:
    raw_text = "PRIVATE ANSWER: secret content abc123"
    result = _python_result(output_text=raw_text)
    comparison = _comparison(ts_digest=TS_DIFFERENT_DIGEST, ts_status="completed")

    summary = compute_shadow_parity(python_result=result, comparison=comparison)
    dumped_str = str(summary.model_dump(by_alias=True, mode="json"))

    # Raw output text must never appear in the serialized summary
    assert raw_text not in dumped_str
    # Only safe/digest fields should be present
    assert "PRIVATE" not in dumped_str
    assert "secret" not in dumped_str


def test_privacy_model_dump_contains_only_digest_and_safe_fields() -> None:
    result = _python_result(output_text="Some diagnostic answer text.")
    comparison = _comparison(ts_digest=TS_DIFFERENT_DIGEST, ts_status="completed")

    summary = compute_shadow_parity(python_result=result, comparison=comparison)
    dumped = summary.model_dump(by_alias=True, mode="json")

    # All string values must be either digests, safe labels, or known literals
    safe_tokens = {
        "gate5b4c3.shadowParity.v1",
        "match",
        "mismatch",
        "incomparable",
        "parity_match",
        "answer_mismatch",
        "status_mismatch",
        "answer_and_status_mismatch",
        "completed",
        "skipped",
        "dropped",
        "error",
        "not_accepted",
        "runner_completed",
        "runner_incomplete",
        "runner_output_missing",
        "runner_timeout",
        "runner_error",
        "input_adapter_drop",
        "adk_primitives_error",
    }
    for key, value in dumped.items():
        if isinstance(value, str):
            assert (
                value.startswith("sha256:")
                or value in safe_tokens
                or value is True
            ), f"Unexpected string value for key {key!r}: {value!r}"
        elif isinstance(value, bool):
            pass  # observe_only=True is fine
        elif value is None:
            pass  # ts fields can be None
        else:
            pass  # other types (int, etc.) are fine


# ---------------------------------------------------------------------------
# Tests — digest format validation
# ---------------------------------------------------------------------------

def test_python_final_answer_digest_is_sha256_prefixed() -> None:
    result = _python_result()
    comparison = _comparison()

    summary = compute_shadow_parity(python_result=result, comparison=comparison)

    assert summary.python_final_answer_digest.startswith("sha256:")
    assert len(summary.python_final_answer_digest) == len("sha256:") + 64


# ---------------------------------------------------------------------------
# Tests — python_terminal_status is a public-safe label
# ---------------------------------------------------------------------------

def test_python_terminal_status_is_safe_label() -> None:
    import re
    result = _python_result(status="completed")
    comparison = _comparison()

    summary = compute_shadow_parity(python_result=result, comparison=comparison)

    # Must match the safe-label regex from the contract
    safe_label_re = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    assert safe_label_re.match(summary.python_terminal_status), (
        f"python_terminal_status {summary.python_terminal_status!r} is not a safe label"
    )


def test_python_terminal_status_for_error_result() -> None:
    result = _python_result(status="error", reason="runner_error")
    comparison = _comparison()

    summary = compute_shadow_parity(python_result=result, comparison=comparison)

    assert summary.python_terminal_status  # must be non-empty
    assert summary.python_terminal_status != ""


def test_python_terminal_status_for_skipped_result() -> None:
    result = _python_result(status="skipped", reason="not_accepted")
    comparison = _comparison()

    summary = compute_shadow_parity(python_result=result, comparison=comparison)

    assert summary.python_terminal_status  # must be non-empty


# ---------------------------------------------------------------------------
# Tests — aliased field names in model_dump (camelCase)
# ---------------------------------------------------------------------------

def test_model_dump_uses_camel_case_aliases() -> None:
    result = _python_result()
    comparison = _comparison()

    summary = compute_shadow_parity(python_result=result, comparison=comparison)
    dumped = summary.model_dump(by_alias=True, mode="json")

    assert "schemaVersion" in dumped
    assert "observeOnly" in dumped
    assert "pythonFinalAnswerDigest" in dumped
    assert "typeScriptFinalAnswerDigest" in dumped
    assert "answerParity" in dumped
    assert "pythonTerminalStatus" in dumped
    assert "typeScriptTerminalStatus" in dumped
    assert "statusParity" in dumped
    assert "verdict" in dumped

    # snake_case keys must NOT appear
    assert "schema_version" not in dumped
    assert "observe_only" not in dumped
    assert "python_final_answer_digest" not in dumped
