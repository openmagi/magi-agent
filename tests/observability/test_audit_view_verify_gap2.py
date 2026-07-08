"""GAP-2 fix: verify per-pass audit rows must not render as UNKNOWN.

RED / GREEN tests for the B3+B4 minimal fix described in
docs/plans/2026-07-07-verify-audit-panel-surfacing-design.md (Section 3.3).

Three golden scenarios:
  (a) A per-pass row with verifyKind="pass"  -> AUDIT PASS / info
  (b) A legacy per-pass row with NO verifyKind -> AUDIT PASS / info (backward-compat)
  (c) Existing verify turn-verdict mapping via verdict_to_display_label is NOT
      broken by the new branch (verified_clean -> VERIFIED CLEAN / pass, etc.)

Hermetic: uses a real ActivityStore on tmp_path, no env, no network.
"""
from __future__ import annotations

import pytest

from magi_agent.evidence.audit_labels import (
    AUDIT_PASS,
    classify_verdict_severity,
    verdict_to_display_label,
)
from magi_agent.observability.audit_view import build_session_audit
from magi_agent.observability.models import ActivityEvent
from magi_agent.observability.store import ActivityStore


def _store(tmp_path):
    return ActivityStore(tmp_path / "obs.db")


def _verify_pass_event(
    *,
    session_id: str,
    run_id: str,
    ts: float,
    verdict: str = "ok",
    include_verify_kind: bool = True,
) -> ActivityEvent:
    """Build a rule_check event that mirrors _emit_verify_pass_observability output.

    ``include_verify_kind=False`` simulates a row emitted by a pre-fix image
    (no verifyKind field) to verify backward-compat (scenario b).
    """
    payload: dict = {
        "verdict": verdict,
        "ruleId": "verify_before_replying.audit",
        "sourceType": "verify",
        "policyId": "verify_before_replying",
        "passIndex": 0,
    }
    if include_verify_kind:
        payload["verifyKind"] = "pass"
    return ActivityEvent(
        kind="rule_check",
        session_id=session_id,
        run_id=run_id,
        ts=ts,
        payload=payload,
    )


# ---------------------------------------------------------------------------
# (a) per-pass row with verifyKind="pass" -> AUDIT PASS / info
# ---------------------------------------------------------------------------


def test_verify_pass_row_with_verify_kind_projects_audit_pass(tmp_path):
    """A per-pass verify row (verifyKind='pass') must render as AUDIT PASS, not UNKNOWN."""
    store = _store(tmp_path)
    store.record_event(
        _verify_pass_event(session_id="s1", run_id="r", ts=1.0, include_verify_kind=True)
    )
    out = build_session_audit("s1", store=store)
    verdict = out["runs"][0]["verdicts"][0]
    assert verdict["displayLabel"] == AUDIT_PASS
    assert verdict["severity"] == "info"
    # status field still carries the raw rule verdict (ok) for internal use
    assert verdict["status"] == "ok"


def test_verify_pass_row_violation_verdict_also_projects_audit_pass(tmp_path):
    """A per-pass row with verdict='violation' (high finding present) still renders
    AUDIT PASS because the row species (verifyKind='pass') overrides the raw
    RuleVerdict -- the pass-row label signals 'this is an audit pass row', not
    'everything was fine'."""
    store = _store(tmp_path)
    store.record_event(
        _verify_pass_event(
            session_id="s1", run_id="r", ts=1.0,
            verdict="violation",
            include_verify_kind=True,
        )
    )
    out = build_session_audit("s1", store=store)
    verdict = out["runs"][0]["verdicts"][0]
    assert verdict["displayLabel"] == AUDIT_PASS
    assert verdict["severity"] == "info"


# ---------------------------------------------------------------------------
# (b) legacy row with NO verifyKind -> AUDIT PASS / info (backward-compat)
# ---------------------------------------------------------------------------


def test_legacy_verify_row_without_verify_kind_projects_audit_pass(tmp_path):
    """A row emitted by a pre-fix image (no verifyKind field) must also render
    AUDIT PASS after the fix so existing stored rows are not left as UNKNOWN."""
    store = _store(tmp_path)
    store.record_event(
        _verify_pass_event(session_id="s1", run_id="r", ts=1.0, include_verify_kind=False)
    )
    out = build_session_audit("s1", store=store)
    verdict = out["runs"][0]["verdicts"][0]
    assert verdict["displayLabel"] == AUDIT_PASS
    assert verdict["severity"] == "info"


def test_legacy_verify_row_pending_verdict_projects_audit_pass(tmp_path):
    """Legacy row with verdict='pending' (advisory-only pass) also renders AUDIT PASS."""
    store = _store(tmp_path)
    store.record_event(
        _verify_pass_event(
            session_id="s1", run_id="r", ts=1.0,
            verdict="pending",
            include_verify_kind=False,
        )
    )
    out = build_session_audit("s1", store=store)
    verdict = out["runs"][0]["verdicts"][0]
    assert verdict["displayLabel"] == AUDIT_PASS
    assert verdict["severity"] == "info"


# ---------------------------------------------------------------------------
# (c) existing verify turn-verdict mapping is NOT broken
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "turn_verdict,expected_label,expected_severity",
    [
        ("verified_clean", "VERIFIED CLEAN", "pass"),
        ("revised", "REVISED", "pass"),
        ("shipped_acknowledged", "SHIPPED ACKNOWLEDGED", "review"),
        ("nudge_ignored", "NUDGE IGNORED", "deny"),
    ],
)
def test_verify_turn_verdict_label_mapping_unchanged(
    turn_verdict: str, expected_label: str, expected_severity: str
) -> None:
    """verdict_to_display_label for verify turn-verdicts must still return the
    four canonical labels unchanged after B4 is applied."""
    label = verdict_to_display_label(turn_verdict, source_type="verify")
    assert label == expected_label
    assert classify_verdict_severity(label) == expected_severity


# ---------------------------------------------------------------------------
# (d) AUDIT_PASS constant exists and has info severity
# ---------------------------------------------------------------------------


def test_audit_pass_constant_and_severity() -> None:
    """AUDIT_PASS label must be exported and classified as info severity."""
    assert AUDIT_PASS == "AUDIT PASS"
    assert classify_verdict_severity(AUDIT_PASS) == "info"


# ---------------------------------------------------------------------------
# (e) non-verify rows are not affected by the new branch
# ---------------------------------------------------------------------------


def test_citation_row_still_projects_sources_cited(tmp_path):
    """The citation override branch in _project_verdict must be unaffected."""
    store = _store(tmp_path)
    store.record_event(
        ActivityEvent(
            kind="rule_check",
            session_id="s1",
            run_id="r",
            ts=1.0,
            payload={
                "verdict": "ok",
                "ruleId": "source_citation.gate",
                "sourceType": "citation",
                "citationVerdict": "cited",
            },
        )
    )
    out = build_session_audit("s1", store=store)
    verdict = out["runs"][0]["verdicts"][0]
    assert verdict["displayLabel"] == "SOURCES CITED"
    assert verdict["severity"] == "pass"


def test_generic_ok_row_unaffected(tmp_path):
    """A generic rule_check row without sourceType='verify' still maps to VERIFIED."""
    store = _store(tmp_path)
    store.record_event(
        ActivityEvent(
            kind="rule_check",
            session_id="s1",
            run_id="r",
            ts=1.0,
            payload={"verdict": "ok", "ruleId": "verifier:sha256:abc"},
        )
    )
    out = build_session_audit("s1", store=store)
    verdict = out["runs"][0]["verdicts"][0]
    assert verdict["displayLabel"] == "VERIFIED"
    assert verdict["severity"] == "pass"
