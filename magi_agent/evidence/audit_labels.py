"""Canonical verdict-status -> display-label projection for the chat Audit panel.

This is the SINGLE place the Audit panel's display vocabulary is defined, so the
chat Audit tab and any other surface (e.g. the run-share render) never drift. It
is a pure, data-driven, deterministic projection with no I/O.

It folds the several runtime verdict-status enums onto ONE small canonical label
set:

  - ``EvidenceVerdictState``  (evidence/types.py):
        audit, pass, missing, failed, block_ready
  - ``VerifierStatus``        (harness/verifier_bus.py):
        pass, failed, missing, approval_required, audit
  - ``ValidatorAction``       (evidence/validator_taxonomy.py):
        pass, repair, ask_user, abstain, block
  - ``HarnessVerifierStatus`` (harness/audit.py):
        started, passed, failed, skipped, error

It additionally recognizes the public ``RuleVerdict`` vocabulary
(``ok``/``violation``/``pending`` — runtime/public_events.py:31/426) because that
is the verdict form actually PERSISTED into the observability store on
``rule_check`` events (the projector copies the public event's ``verdict`` field,
not a runtime enum). Mapping those is required for the read surface to produce a
meaningful label; it is purely additive and conflicts with none of the enum
rules above.
"""

from __future__ import annotations

from magi_agent.observability.taxonomy import policy_event_kinds

# --- canonical label set (UPPERCASE) ---------------------------------------
VERIFIED = "VERIFIED"
FAILED = "FAILED"
BLOCKED = "BLOCKED"
REJECTED_BY_REVIEWER = "REJECTED BY REVIEWER"
NEEDS_APPROVAL = "NEEDS APPROVAL"
REPAIRED = "REPAIRED"
ABSTAINED = "ABSTAINED"
AUDIT = "AUDIT"
MISSING = "MISSING"
PENDING = "PENDING"
UNKNOWN = "UNKNOWN"

# Source-citation gate labels (Wave 4b). The source_citation.gate producer emits
# a rule_check-family observability event tagged source_type="citation"; its raw
# citation verdict (cited/partial/uncited) projects to these dedicated labels so
# the Audit tab renders the same citation-governance vocabulary the Wave 3b
# client projection used, rather than the generic pass/violation labels.
SOURCES_CITED = "SOURCES CITED"
PARTIALLY_CITED = "PARTIALLY CITED"
UNCITED_CLAIMS = "UNCITED CLAIMS"

# Verify-before-replying turn-level verdict labels (PR-V4, design Section 12.5).
# The verify_before_replying.audit producer emits a custom:VerifyReplyVerdict
# evidence record tagged source_type="verify"; its raw verdict string projects
# to these four canonical labels. The mapping follows the _CITATION_VERDICT_TO_LABEL
# pattern so the Audit tab renders consistent governance vocabulary across both
# citation and verify surfaces.
VERIFIED_CLEAN = "VERIFIED CLEAN"
REVISED = "REVISED"
SHIPPED_ACKNOWLEDGED = "SHIPPED ACKNOWLEDGED"
NUDGE_IGNORED = "NUDGE IGNORED"

# Statuses that count as a "pass" for the reviewer special-case below. A reviewer
# event with any NON-pass status projects to REJECTED BY REVIEWER.
_PASS_STATUSES: frozenset[str] = frozenset({"pass", "passed", "ok"})

# Raw citation-verdict value (source_type == "citation") -> canonical label. The
# source_citation gate never emits ``not_applicable`` to the Audit surface (a
# no-source turn produces no enforcement row), so only the three actionable
# verdicts are mapped here.
_CITATION_VERDICT_TO_LABEL: dict[str, str] = {
    "cited": SOURCES_CITED,
    "partial": PARTIALLY_CITED,
    "uncited": UNCITED_CLAIMS,
}

# Raw verify turn-verdict value (source_type == "verify") -> canonical label. The
# four verdicts map the final per-turn disposition of all findings. The verdict is
# computed inside _emit_verify_reply_verdict (driver.py) from the resolution
# taxonomy (design Section 12.3): verified_clean (zero high findings) / revised
# (all high findings resolved by model revision) / shipped_acknowledged
# (findings present, ship marker used) / nudge_ignored (findings present, no
# marker -- the key metric). An unrecognized verdict falls back to the generic
# status map via verdict_to_display_label's final return.
_VERIFY_VERDICT_TO_LABEL: dict[str, str] = {
    "verified_clean": VERIFIED_CLEAN,
    "revised": REVISED,
    "shipped_acknowledged": SHIPPED_ACKNOWLEDGED,
    "nudge_ignored": NUDGE_IGNORED,
}

# Normalized (lowercased) status string -> canonical display label.
_STATUS_TO_LABEL: dict[str, str] = {
    # pass family
    "pass": VERIFIED,
    "passed": VERIFIED,
    "ok": VERIFIED,  # RuleVerdict (persisted)
    # failure family
    "failed": FAILED,
    "error": FAILED,
    # block family
    "block": BLOCKED,
    "block_ready": BLOCKED,
    "blocked": BLOCKED,
    "denied": BLOCKED,
    "deny": BLOCKED,
    "violation": BLOCKED,  # RuleVerdict (persisted)
    # reviewer rejection
    "rejected": REJECTED_BY_REVIEWER,
    # approval family
    "approval_required": NEEDS_APPROVAL,
    "ask_user": NEEDS_APPROVAL,
    # validator repair / abstain
    "repair": REPAIRED,
    "abstain": ABSTAINED,
    # audit-only
    "audit": AUDIT,
    # missing / skipped
    "missing": MISSING,
    "skipped": MISSING,
    # in-flight / pending
    "started": PENDING,
    "pending": PENDING,
}

# Display label -> frontend badge bucket. Decided server-side so every surface
# renders the same badge variant for the same verdict.
_SEVERITY_BY_LABEL: dict[str, str] = {
    VERIFIED: "pass",
    REPAIRED: "pass",
    FAILED: "deny",
    BLOCKED: "deny",
    REJECTED_BY_REVIEWER: "deny",
    NEEDS_APPROVAL: "review",
    ABSTAINED: "review",
    AUDIT: "review",
    MISSING: "review",
    PENDING: "info",
    UNKNOWN: "info",
    # Source-citation gate: a fully cited answer passes; partial or uncited are
    # review-worthy (never a hard deny, the gate is advisory/fail-open).
    SOURCES_CITED: "pass",
    PARTIALLY_CITED: "review",
    UNCITED_CLAIMS: "review",
    # Verify-before-replying turn-level verdicts (PR-V4). Clean/revised pass;
    # shipped-acknowledged is review-worthy; nudge_ignored is a deny-level signal
    # (the key operational metric for ignore-rate tracking).
    VERIFIED_CLEAN: "pass",
    REVISED: "pass",
    SHIPPED_ACKNOWLEDGED: "review",
    NUDGE_IGNORED: "deny",
}

# ---------------------------------------------------------------------------
# Enforcement event kinds.
#
# These are the ONLY event ``kind`` strings persisted to the observability store
# (.openmagi/observability.db) that carry a policy/guardrail/reviewer/rule
# enforcement verdict. Verified directly from the persistence paths:
#
#   - "rule_check":    written by ActivityStore via project_public_event() when a
#                      public event's ``type`` == "rule_check"
#                      (magi_agent/runtime/public_events.py:426-440 rule_check_event;
#                      emitters magi_agent/evidence/event_projection.py:98/112).
#                      Also the "policy" taxonomy category
#                      (magi_agent/observability/taxonomy.py:54-56) and the store's
#                      evidence pre-filter pins this exact kind
#                      (magi_agent/observability/store.py:268-269).
#   - "rule_violation": written by ActivityStore via project() from the
#                      onRuleViolation hook point
#                      (magi_agent/observability/projector.py:19 maps
#                      "onRuleViolation" -> ("rule_violation", "blocked")). Also the
#                      "policy" taxonomy category (taxonomy.py:56).
#
# Deliberately EXCLUDED:
#   - "policy_decision" / "guardrail_observed": these exist only as ops *metric*
#     label constants (magi_agent/ops/metrics.py:31-32); they are never projected
#     into an ActivityEvent.kind by project()/project_public_event(), so they are
#     never persisted to this store. (The RuntimeOperationEvent stream is a
#     separate, non-persisted surface.)
#   - reviewer verdicts: cross_review/peer-reviewer decisions reach the store
#     through "rule_check" events; there is no dedicated persisted "reviewer"
#     kind today. The reviewer label is selected via the ``source_type`` argument
#     of verdict_to_display_label, read from the event payload.
#
# Single source of truth: derived from the observability taxonomy's "policy"
# category (taxonomy.CATEGORIES["policy"] == ["rule_check", "rule_violation"]),
# whose docstring forbids duplicating that mapping elsewhere on the server.
# taxonomy.py imports nothing from magi_agent, so importing it here (evidence/
# is a lower layer than observability/) does NOT create a circular import
# (verified via `python -c "import magi_agent.evidence.audit_labels"`).
# ---------------------------------------------------------------------------
ENFORCEMENT_EVENT_KINDS: frozenset[str] = frozenset(policy_event_kinds())


def verdict_to_display_label(status: str, *, source_type: str | None = None) -> str:
    """Project a runtime verdict ``status`` onto a canonical display label.

    Case-insensitive on ``status``. A reviewer event (``source_type == "reviewer"``)
    with any non-pass status projects to ``REJECTED BY REVIEWER``. Unrecognized
    statuses project to ``UNKNOWN``.
    """
    normalized = (status or "").strip().lower()
    source = source_type.strip().lower() if source_type is not None else ""
    if source == "reviewer":
        if normalized not in _PASS_STATUSES:
            return REJECTED_BY_REVIEWER
    if source == "citation":
        # ``normalized`` is the raw citation verdict (cited/partial/uncited). An
        # unrecognized value falls back to the generic status map so the row is
        # never lost.
        citation_label = _CITATION_VERDICT_TO_LABEL.get(normalized)
        if citation_label is not None:
            return citation_label
    if source == "verify":
        # ``normalized`` is the raw verify turn-verdict (verified_clean / revised
        # / shipped_acknowledged / nudge_ignored). An unrecognized value falls
        # back to UNKNOWN below (the verify surface never emits generic statuses).
        verify_label = _VERIFY_VERDICT_TO_LABEL.get(normalized)
        if verify_label is not None:
            return verify_label
        return UNKNOWN
    return _STATUS_TO_LABEL.get(normalized, UNKNOWN)


def classify_verdict_severity(label: str) -> str:
    """Return the frontend badge bucket for a canonical display ``label``.

    Buckets: ``pass`` / ``deny`` / ``review`` / ``info``. Any unknown label
    falls back to ``info``.
    """
    return _SEVERITY_BY_LABEL.get(label, "info")


def is_enforced_kind(kind: str) -> bool:
    """Return True iff ``kind`` is a persisted policy-enforcement event kind."""
    return kind in ENFORCEMENT_EVENT_KINDS


__all__ = [
    "ENFORCEMENT_EVENT_KINDS",
    "NUDGE_IGNORED",
    "REVISED",
    "SHIPPED_ACKNOWLEDGED",
    "VERIFIED_CLEAN",
    "classify_verdict_severity",
    "is_enforced_kind",
    "verdict_to_display_label",
]
