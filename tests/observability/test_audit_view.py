"""Per-session audit projection: grouping, ordering, label/severity, redaction.

Uses a real ActivityStore on a tmp_path SQLite db. Hermetic (no env, no network).
"""
from __future__ import annotations

from magi_agent.observability.audit_view import build_session_audit
from magi_agent.observability.models import ActivityEvent
from magi_agent.observability.store import ActivityStore


def _store(tmp_path):
    return ActivityStore(tmp_path / "obs.db")


def _rule_check(*, session_id, run_id, ts, verdict, rule_id, detail=None, evidence_ref=None):
    payload = {"verdict": verdict, "ruleId": rule_id}
    if detail is not None:
        payload["detail"] = detail
    if evidence_ref is not None:
        payload["evidenceRef"] = evidence_ref
    return ActivityEvent(
        kind="rule_check",
        session_id=session_id,
        run_id=run_id,
        ts=ts,
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Grouping + ordering
# ---------------------------------------------------------------------------


def test_groups_by_run_id_newest_first(tmp_path):
    store = _store(tmp_path)
    store.record_event(_rule_check(session_id="s1", run_id="run-a", ts=100.0, verdict="ok", rule_id="verifier:sha256:aaa"))
    store.record_event(_rule_check(session_id="s1", run_id="run-a", ts=110.0, verdict="violation", rule_id="verifier:sha256:bbb"))
    store.record_event(_rule_check(session_id="s1", run_id="run-b", ts=200.0, verdict="ok", rule_id="verifier:sha256:ccc"))

    out = build_session_audit("s1", store=store)
    assert out["ok"] is True
    assert out["sessionId"] == "s1"
    run_ids = [r["runId"] for r in out["runs"]]
    assert run_ids == ["run-b", "run-a"]  # newest (max ts) first

    run_a = next(r for r in out["runs"] if r["runId"] == "run-a")
    assert run_a["policyCount"] == 2
    assert run_a["startedAt"] == 100.0
    assert len(run_a["verdicts"]) == 2


def test_label_and_severity_projection(tmp_path):
    store = _store(tmp_path)
    store.record_event(_rule_check(session_id="s1", run_id="r", ts=1.0, verdict="ok", rule_id="verifier:sha256:a", detail="verifier status=pass"))
    store.record_event(_rule_check(session_id="s1", run_id="r", ts=2.0, verdict="violation", rule_id="evidence:sha256:b", detail="evidence verdict state=failed"))

    out = build_session_audit("s1", store=store)
    verdicts = out["runs"][0]["verdicts"]
    by_subject = {v["subject"]: v for v in verdicts}

    ok = by_subject["verifier:sha256:a"]
    assert ok["displayLabel"] == "VERIFIED"
    assert ok["severity"] == "pass"
    assert ok["status"] == "ok"
    assert ok["summary"] == "verifier status=pass"
    assert ok["kind"] == "rule_check"

    viol = by_subject["evidence:sha256:b"]
    assert viol["displayLabel"] == "BLOCKED"
    assert viol["severity"] == "deny"


def test_rule_violation_status_blocked(tmp_path):
    store = _store(tmp_path)
    # Mirrors projector: onRuleViolation -> kind=rule_violation, status="blocked".
    store.record_event(
        ActivityEvent(kind="rule_violation", session_id="s1", run_id="r", ts=5.0, status="blocked", summary="policy violated")
    )
    out = build_session_audit("s1", store=store)
    verdict = out["runs"][0]["verdicts"][0]
    assert verdict["status"] == "blocked"
    assert verdict["displayLabel"] == "BLOCKED"
    assert verdict["severity"] == "deny"


def test_reviewer_payload_source_type(tmp_path):
    store = _store(tmp_path)
    ev = ActivityEvent(
        kind="rule_check",
        session_id="s1",
        run_id="r",
        ts=1.0,
        payload={"verdict": "violation", "ruleId": "reviewer:sha256:x", "sourceType": "reviewer"},
    )
    store.record_event(ev)
    out = build_session_audit("s1", store=store)
    assert out["runs"][0]["verdicts"][0]["displayLabel"] == "REJECTED BY REVIEWER"


def test_evidence_refs_surfaced(tmp_path):
    store = _store(tmp_path)
    digest = "sha256:" + "a" * 64
    store.record_event(_rule_check(session_id="s1", run_id="r", ts=1.0, verdict="ok", rule_id="verifier:sha256:a", evidence_ref=digest))
    out = build_session_audit("s1", store=store)
    assert out["runs"][0]["verdicts"][0]["evidenceRefs"] == [digest]


def test_non_enforcement_events_excluded(tmp_path):
    store = _store(tmp_path)
    store.record_event(ActivityEvent(kind="tool_start", session_id="s1", run_id="r", ts=1.0))
    store.record_event(ActivityEvent(kind="tool_end", session_id="s1", run_id="r", ts=2.0))
    store.record_event(ActivityEvent(kind="turn_start", session_id="s1", run_id="r", ts=3.0))
    out = build_session_audit("s1", store=store)
    assert out["runs"] == []


def test_empty_session(tmp_path):
    store = _store(tmp_path)
    out = build_session_audit("nonexistent", store=store)
    assert out == {"ok": True, "sessionId": "nonexistent", "runs": [], "sources": []}


def test_ungrouped_bucket_for_missing_run_id(tmp_path):
    store = _store(tmp_path)
    store.record_event(_rule_check(session_id="s1", run_id=None, ts=1.0, verdict="ok", rule_id="verifier:sha256:a"))
    out = build_session_audit("s1", store=store)
    assert len(out["runs"]) == 1
    assert out["runs"][0]["runId"] is None
    assert out["runs"][0]["policyCount"] == 1


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_summary_is_redacted(tmp_path):
    store = _store(tmp_path)
    # Assemble a GitHub-token-shaped secret from fragments (never a contiguous
    # real-looking literal — push protection would block that).
    secret = "gh" + "p" + "_" + "0A1b2C3d4E5f6071829304a5b6c7d8e9f0a1"
    store.record_event(
        ActivityEvent(
            kind="rule_violation",
            session_id="s1",
            run_id="r",
            ts=1.0,
            status="blocked",
            summary=f"leaked token {secret} in output",
        )
    )
    out = build_session_audit("s1", store=store)
    summary = out["runs"][0]["verdicts"][0]["summary"]
    assert secret not in summary
    assert "[redacted]" in summary


# ---------------------------------------------------------------------------
# Sources projection
# ---------------------------------------------------------------------------


def test_sources_projection_verified_vs_unverified(tmp_path):
    store = _store(tmp_path)
    records = [
        {"source_id": "src_1", "title": "Primary doc", "uri": "ref:src_1", "inspected": True, "trust_tier": "primary"},
        {"source_id": "src_2", "title": "Unverified blog", "uri": "ref:src_2", "inspected": True, "trust_tier": "secondary"},
        {"source_id": "src_3", "title": "Not inspected", "uri": "ref:src_3", "inspected": False, "trust_tier": "official"},
    ]
    out = build_session_audit("s1", store=store, source_records=records)
    sources = {s["label"]: s for s in out["sources"]}

    assert sources["Primary doc"]["verified"] is True
    assert sources["Primary doc"]["credibility"] == "credible"
    assert sources["Unverified blog"]["verified"] is False
    assert sources["Unverified blog"]["credibility"] == "unverified"
    # inspected=False with official tier is still not verified
    assert sources["Not inspected"]["verified"] is False


def test_sources_dedupe_by_uri(tmp_path):
    store = _store(tmp_path)
    records = [
        {"source_id": "src_1", "title": "A", "uri": "ref:dup", "inspected": True, "trust_tier": "primary"},
        {"source_id": "src_2", "title": "B", "uri": "ref:dup", "inspected": True, "trust_tier": "primary"},
    ]
    out = build_session_audit("s1", store=store, source_records=records)
    assert len(out["sources"]) == 1


def test_sources_explicit_contradicted_credibility(tmp_path):
    store = _store(tmp_path)
    records = [
        {"source_id": "src_1", "title": "Disputed", "uri": "ref:src_1", "inspected": True, "trust_tier": "primary", "credibility": "contradicted"},
    ]
    out = build_session_audit("s1", store=store, source_records=records)
    assert out["sources"][0]["credibility"] == "contradicted"


# ---------------------------------------------------------------------------
# FIX-1: kind push-down truncation regression
# ---------------------------------------------------------------------------


def test_enforcement_survives_noise_truncation(tmp_path):
    """Many noise rows (> limit) recorded BEFORE a few enforcement rows must not
    truncate the enforcement verdicts out of the window.

    Pre-fix, build_session_audit read the first `limit` rows by id ASC and
    Python-filtered, so noise filled the window and the (higher-id) enforcement
    rows were dropped. Post-fix, the kind filter is pushed down to SQL.
    """
    store = _store(tmp_path)
    limit = 10
    # Record more noise rows than `limit`, all with LOWER ids than enforcement.
    for i in range(limit * 3):
        store.record_event(
            ActivityEvent(kind="text_delta", session_id="s1", run_id="r", ts=float(i))
        )
    # A few sparse enforcement rows, recorded last (highest ids).
    store.record_event(_rule_check(session_id="s1", run_id="r", ts=1000.0, verdict="ok", rule_id="verifier:sha256:a"))
    store.record_event(_rule_check(session_id="s1", run_id="r", ts=1001.0, verdict="violation", rule_id="verifier:sha256:b"))

    out = build_session_audit("s1", store=store, limit=limit)
    labels = {v["displayLabel"] for run in out["runs"] for v in run["verdicts"]}
    assert labels == {"VERIFIED", "BLOCKED"}
    assert sum(len(run["verdicts"]) for run in out["runs"]) == 2


# ---------------------------------------------------------------------------
# FIX-3: evidenceRefs redaction + dedupe
# ---------------------------------------------------------------------------


def test_evidence_refs_redacted_and_deduped(tmp_path):
    store = _store(tmp_path)
    hash_ref = "verifier:sha256:" + "a" * 64
    url_ref = "https://evil.example.com/leak?token=zzz"
    store.record_event(
        ActivityEvent(
            kind="rule_check",
            session_id="s1",
            run_id="r",
            ts=1.0,
            payload={
                "verdict": "ok",
                "ruleId": "verifier:sha256:a",
                # hash-shaped survives, url-shaped redacted+dropped, dup deduped.
                "evidenceRefs": [hash_ref, url_ref, hash_ref],
            },
        )
    )
    out = build_session_audit("s1", store=store)
    refs = out["runs"][0]["verdicts"][0]["evidenceRefs"]
    assert refs == [hash_ref]
    assert "evil.example.com" not in str(refs)


# ---------------------------------------------------------------------------
# FIX-4: source uri host-preservation
# ---------------------------------------------------------------------------


def test_source_uri_keeps_host_drops_query(tmp_path):
    store = _store(tmp_path)
    records = [
        {"source_id": "s", "title": "SEC filing", "uri": "https://www.sec.gov/secfiling/123?token=secretzzz#frag", "inspected": True, "trust_tier": "primary"},
    ]
    out = build_session_audit("s1", store=store, source_records=records)
    uri = out["sources"][0]["uri"]
    assert uri == "www.sec.gov/secfiling/…"
    assert "token" not in uri and "secretzzz" not in uri and "frag" not in uri


def test_source_uri_ref_locator_redacted(tmp_path):
    store = _store(tmp_path)
    records = [
        {"source_id": "s", "title": "Internal", "uri": "ref:src_internal_1", "inspected": True, "trust_tier": "primary"},
    ]
    out = build_session_audit("s1", store=store, source_records=records)
    assert out["sources"][0]["uri"] == "[redacted]"


def test_source_uri_strips_userinfo(tmp_path):
    store = _store(tmp_path)
    records = [
        {"source_id": "s", "title": "Auth url", "uri": "https://user:pass@cnbc.com/markets", "inspected": True, "trust_tier": "primary"},
    ]
    out = build_session_audit("s1", store=store, source_records=records)
    uri = out["sources"][0]["uri"]
    assert uri == "cnbc.com/markets"
    assert "user" not in uri and "pass" not in uri


# ---------------------------------------------------------------------------
# FIX-8: subject redaction + all-None-ts run
# ---------------------------------------------------------------------------


def test_subject_redacted_when_rule_id_contains_secret(tmp_path):
    store = _store(tmp_path)
    # Assemble a GitHub-token-shaped secret from fragments (push protection).
    secret = "gh" + "p" + "_" + "0A1b2C3d4E5f6071829304a5b6c7d8e9f0a1"
    store.record_event(
        ActivityEvent(
            kind="rule_check",
            session_id="s1",
            run_id="r",
            ts=1.0,
            payload={"verdict": "violation", "ruleId": f"rule-{secret}"},
        )
    )
    out = build_session_audit("s1", store=store)
    subject = out["runs"][0]["verdicts"][0]["subject"]
    assert secret not in (subject or "")


class _StubStore:
    """Minimal store stub returning rows with non-numeric (None) timestamps to
    exercise the all-None-ts branch the real (ts REAL NOT NULL) store cannot."""

    def __init__(self, rows):
        self._rows = rows

    def list_events(self, *, session_id, kind=None, limit=200):
        return list(self._rows)


def test_all_none_ts_run_still_appears():
    rows = [
        {"id": 1, "kind": "rule_check", "run_id": "r", "ts": None, "payload": {"verdict": "ok", "ruleId": "verifier:sha256:a"}},
        {"id": 2, "kind": "rule_check", "run_id": "r", "ts": None, "payload": {"verdict": "violation", "ruleId": "verifier:sha256:b"}},
    ]
    out = build_session_audit("s1", store=_StubStore(rows))
    assert len(out["runs"]) == 1
    assert out["runs"][0]["startedAt"] is None
    assert out["runs"][0]["policyCount"] == 2


# ---------------------------------------------------------------------------
# Source-citation gate verdict (Wave 4b Piece E)
# ---------------------------------------------------------------------------


def _citation_event(*, session_id, run_id, ts, citation_verdict, verdict, **scalars):
    payload = {
        "verdict": verdict,
        "ruleId": "source_citation.gate",
        "sourceType": "citation",
        "citationVerdict": citation_verdict,
    }
    payload.update(scalars)
    return ActivityEvent(
        kind="rule_check",
        session_id=session_id,
        run_id=run_id,
        ts=ts,
        payload=payload,
    )


def test_citation_gate_verdict_projects_dedicated_label(tmp_path):
    store = _store(tmp_path)
    store.record_event(
        _citation_event(
            session_id="s1",
            run_id="r",
            ts=1.0,
            citation_verdict="cited",
            verdict="ok",
        )
    )
    out = build_session_audit("s1", store=store)
    verdict = out["runs"][0]["verdicts"][0]
    assert verdict["subject"] == "source_citation.gate"
    assert verdict["displayLabel"] == "SOURCES CITED"
    assert verdict["severity"] == "pass"


def test_citation_gate_partial_is_review(tmp_path):
    store = _store(tmp_path)
    store.record_event(
        _citation_event(
            session_id="s1",
            run_id="r",
            ts=1.0,
            citation_verdict="partial",
            verdict="pending",
        )
    )
    verdict = build_session_audit("s1", store=store)["runs"][0]["verdicts"][0]
    assert verdict["displayLabel"] == "PARTIALLY CITED"
    assert verdict["severity"] == "review"


def test_citation_gate_affordances_project_as_reason_codes(tmp_path):
    store = _store(tmp_path)
    store.record_event(
        _citation_event(
            session_id="s1",
            run_id="r",
            ts=1.0,
            citation_verdict="uncited",
            verdict="violation",
            repairAttempts=2,
            inducedSearch=True,
            failOpen=True,
        )
    )
    verdict = build_session_audit("s1", store=store)["runs"][0]["verdicts"][0]
    assert verdict["displayLabel"] == "UNCITED CLAIMS"
    assert verdict["severity"] == "review"
    assert "repaired (2)" in verdict["affordances"]
    assert "induced search" in verdict["affordances"]
    assert "fail-open" in verdict["affordances"]
    # Affordances ride their own list, not the generic reason-code chips.
    assert verdict["reasonCodes"] == []


def test_citation_gate_no_affordances_no_extra_codes(tmp_path):
    store = _store(tmp_path)
    store.record_event(
        _citation_event(
            session_id="s1",
            run_id="r",
            ts=1.0,
            citation_verdict="cited",
            verdict="ok",
            repairAttempts=0,
            inducedSearch=False,
            failOpen=False,
        )
    )
    verdict = build_session_audit("s1", store=store)["runs"][0]["verdicts"][0]
    assert verdict["reasonCodes"] == []
    assert verdict["affordances"] == []


def test_non_citation_row_has_no_affordances(tmp_path):
    store = _store(tmp_path)
    store.record_event(
        _rule_check(session_id="s1", run_id="r", ts=1.0, verdict="ok", rule_id="verifier:sha256:a")
    )
    verdict = build_session_audit("s1", store=store)["runs"][0]["verdicts"][0]
    assert verdict["affordances"] == []


# ---------------------------------------------------------------------------
# Verify turn-verdict twin projection (PR-1)
# ---------------------------------------------------------------------------


def _verify_turn_event(*, session_id, run_id, ts, verify_verdict, verdict, **scalars):
    payload = {
        "verdict": verdict,
        "ruleId": "verify_before_replying.audit",
        "sourceType": "verify",
        "verifyKind": "turn",
        "verifyVerdict": verify_verdict,
    }
    payload.update(scalars)
    return ActivityEvent(
        kind="rule_check",
        session_id=session_id,
        run_id=run_id,
        ts=ts,
        payload=payload,
    )


def test_project_verdict_verify_turn_row(tmp_path):
    """B4 turn arm: verifyKind=='turn' rows project to the correct display label
    and carry a 'verify' wire object with all scalar fields."""
    store = _store(tmp_path)
    store.record_event(
        _verify_turn_event(
            session_id="s1",
            run_id="r",
            ts=1.0,
            verify_verdict="revised",
            verdict="ok",
            passes=2,
            highTotal=1,
            highResolved=1,
            highAcknowledged=0,
            highIgnored=0,
            advisoryTotal=0,
            advisoryIgnored=0,
            shipMarkerUsed=False,
            loopBackToolCalls=3,
            skepticRan=False,
            corpusRecordCount=12,
            detail="verify verdict=revised: passes=2 high=1/1 resolved, loopback_tools=3",
        )
    )
    out = build_session_audit("s1", store=store)
    row = out["runs"][0]["verdicts"][0]
    assert row["displayLabel"] == "REVISED"
    assert row["severity"] == "pass"
    assert row["status"] == "revised"
    # verify wire object must be present with all scalar fields.
    verify_obj = row.get("verify")
    assert verify_obj is not None, "Expected 'verify' wire object on turn row"
    assert verify_obj["kind"] == "turn"
    assert verify_obj["verdict"] == "revised"
    assert verify_obj["passes"] == 2
    assert verify_obj["loopBackToolCalls"] == 3
    assert verify_obj["shipMarkerUsed"] is False
    assert verify_obj["highTotal"] == 1
    assert verify_obj["highResolved"] == 1
    assert verify_obj["highAcknowledged"] == 0
    assert verify_obj["highIgnored"] == 0
    assert verify_obj["advisoryTotal"] == 0
    assert verify_obj["advisoryIgnored"] == 0
    assert verify_obj["corpusRecordCount"] == 12
    # findingsOmitted and context must be absent when not in payload.
    assert "findingsOmitted" not in verify_obj
    assert "context" not in verify_obj


def test_project_verdict_verify_turn_nudge_ignored(tmp_path):
    """nudge_ignored verdict maps to NUDGE IGNORED / deny severity."""
    store = _store(tmp_path)
    store.record_event(
        _verify_turn_event(
            session_id="s1",
            run_id="r",
            ts=1.0,
            verify_verdict="nudge_ignored",
            verdict="violation",
            passes=2,
            highTotal=1,
            highResolved=0,
            highAcknowledged=0,
            highIgnored=1,
            advisoryTotal=0,
            advisoryIgnored=0,
            shipMarkerUsed=False,
            loopBackToolCalls=0,
            skepticRan=False,
            corpusRecordCount=5,
        )
    )
    row = build_session_audit("s1", store=store)["runs"][0]["verdicts"][0]
    assert row["displayLabel"] == "NUDGE IGNORED"
    assert row["severity"] == "deny"


def test_project_verdict_verify_turn_unknown_verdict_never_crashes(tmp_path):
    """Unknown verifyVerdict falls back to UNKNOWN via audit_labels without crashing.
    The verify wire object is still present with kind=='turn'."""
    store = _store(tmp_path)
    store.record_event(
        _verify_turn_event(
            session_id="s1",
            run_id="r",
            ts=1.0,
            verify_verdict="garbage",
            verdict="pending",
        )
    )
    row = build_session_audit("s1", store=store)["runs"][0]["verdicts"][0]
    # Must not raise; label may be UNKNOWN or any fallback, severity info.
    assert isinstance(row["displayLabel"], str)
    assert row["severity"] == "info"
    verify_obj = row.get("verify")
    assert verify_obj is not None
    assert verify_obj["kind"] == "turn"


def test_project_verdict_verify_pass_row_gains_kind_object(tmp_path):
    """U1's pass arm (verifyKind=='pass') now also carries verify=={'kind': 'pass'}.
    Display label and severity remain AUDIT PASS / info (U1 behavior unchanged)."""
    store = _store(tmp_path)
    store.record_event(
        ActivityEvent(
            kind="rule_check",
            session_id="s1",
            run_id="r",
            ts=1.0,
            payload={
                "verdict": "ok",
                "ruleId": "verify_before_replying.audit",
                "sourceType": "verify",
                "verifyKind": "pass",
            },
        )
    )
    row = build_session_audit("s1", store=store)["runs"][0]["verdicts"][0]
    assert row["displayLabel"] == "AUDIT PASS"
    assert row["severity"] == "info"
    verify_obj = row.get("verify")
    assert verify_obj is not None, "Expected verify wire object on pass row"
    assert verify_obj == {"kind": "pass"}


def test_project_verdict_legacy_verify_row_has_no_verify_object(tmp_path):
    """Legacy rows (sourceType==verify, no verifyKind) keep U1 behavior
    (AUDIT PASS / info) and must NOT gain a half-filled verify object."""
    store = _store(tmp_path)
    store.record_event(
        ActivityEvent(
            kind="rule_check",
            session_id="s1",
            run_id="r",
            ts=1.0,
            payload={
                "verdict": "ok",
                "ruleId": "verify_before_replying.audit",
                "sourceType": "verify",
                # No verifyKind key at all (legacy row).
            },
        )
    )
    row = build_session_audit("s1", store=store)["runs"][0]["verdicts"][0]
    assert row["displayLabel"] == "AUDIT PASS"
    assert row["severity"] == "info"
    # Legacy row must NOT have a verify wire object.
    assert row.get("verify") is None
