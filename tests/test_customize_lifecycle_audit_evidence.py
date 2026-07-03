"""Evidence-grounded lifecycle-audit judge (PR5 seam).

The shared audit judge ``_audit_one_rule`` (and the turn-boundary fan-out
wrappers that forward to it) can judge a criterion against a scoped projection
of the evidence ledger when the rule declares ``evidenceRefs`` and the caller
supplies ``evidence_records`` (with ``MAGI_EVIDENCE_GROUNDED_CRITIC_ENABLED``
on). Byte-identical (evidence-blind) otherwise. Fail-open throughout.
"""

from __future__ import annotations

import asyncio

from magi_agent.customize.lifecycle_audit import (
    _audit_one_rule,
    run_after_turn_end_audit,
)
from magi_agent.customize.verification_policy import CustomizeVerificationPolicy


def _rule(*, criterion="the change is covered by a passing test", evidence_refs=None):
    payload = {"criterion": criterion}
    if evidence_refs is not None:
        payload["evidenceRefs"] = evidence_refs
    return {
        "id": "cr_audit_ev",
        "scope": "always",
        "enabled": True,
        "what": {"kind": "llm_criterion", "payload": payload},
        "firesAt": "after_turn_end",
        "action": "audit",
    }


def _capture_invoke(seen):
    async def invoke(_model, prompt):
        seen.append(prompt)
        return '{"pass": true, "reason": "ok"}'

    return invoke


_RECORDS = [{"type": "TestRun", "fields": {"exit_code": 0}}]


def test_audit_one_rule_evidence_reaches_judge(monkeypatch):
    monkeypatch.setenv("MAGI_EVIDENCE_GROUNDED_CRITIC_ENABLED", "1")
    seen: list[str] = []
    audit = asyncio.run(
        _audit_one_rule(
            _rule(evidence_refs=["TestRun", "GitDiff"]),
            draft_text="done",
            model_factory=lambda: object(),
            invoke=_capture_invoke(seen),
            evidence_records=_RECORDS,
        )
    )
    assert audit["status"] == "evaluated"
    assert len(seen) == 1
    assert "UNTRUSTED_EVIDENCE" in seen[0]
    assert "TestRun" in seen[0]
    # declared-but-absent evidence is surfaced so the judge can reason about it
    assert "GitDiff" in seen[0]


def test_audit_one_rule_evidence_blind_without_records(monkeypatch):
    monkeypatch.setenv("MAGI_EVIDENCE_GROUNDED_CRITIC_ENABLED", "1")
    seen: list[str] = []
    asyncio.run(
        _audit_one_rule(
            _rule(evidence_refs=["TestRun"]),
            draft_text="done",
            model_factory=lambda: object(),
            invoke=_capture_invoke(seen),
            evidence_records=None,
        )
    )
    assert len(seen) == 1
    assert "UNTRUSTED_EVIDENCE" not in seen[0]


def test_audit_one_rule_evidence_blind_when_flag_off(monkeypatch):
    monkeypatch.setenv("MAGI_EVIDENCE_GROUNDED_CRITIC_ENABLED", "0")
    seen: list[str] = []
    asyncio.run(
        _audit_one_rule(
            _rule(evidence_refs=["TestRun"]),
            draft_text="done",
            model_factory=lambda: object(),
            invoke=_capture_invoke(seen),
            evidence_records=_RECORDS,
        )
    )
    assert len(seen) == 1
    assert "UNTRUSTED_EVIDENCE" not in seen[0]


def test_audit_one_rule_evidence_blind_without_evidence_refs(monkeypatch):
    # No evidenceRefs on the rule → evidence-blind even with flag on + records.
    monkeypatch.setenv("MAGI_EVIDENCE_GROUNDED_CRITIC_ENABLED", "1")
    seen: list[str] = []
    asyncio.run(
        _audit_one_rule(
            _rule(),
            draft_text="done",
            model_factory=lambda: object(),
            invoke=_capture_invoke(seen),
            evidence_records=_RECORDS,
        )
    )
    assert len(seen) == 1
    assert "UNTRUSTED_EVIDENCE" not in seen[0]


def test_run_after_turn_end_audit_forwards_evidence(monkeypatch):
    # End-to-end through the public fan-out wrapper.
    for flag in (
        "MAGI_CUSTOMIZE_LIFECYCLE_TURN_HOOKS_ENABLED",
        "MAGI_CUSTOMIZE_VERIFICATION_ENABLED",
        "MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED",
        "MAGI_EVIDENCE_GROUNDED_CRITIC_ENABLED",
    ):
        monkeypatch.setenv(flag, "1")
    seen: list[str] = []
    policy = CustomizeVerificationPolicy(custom_rules=(_rule(evidence_refs=["TestRun"]),))
    audits = asyncio.run(
        run_after_turn_end_audit(
            final_text="the diff is done",
            model_factory=lambda: object(),
            invoke=_capture_invoke(seen),
            policy_loader=lambda: policy,
            evidence_records=_RECORDS,
        )
    )
    assert len(audits) == 1
    assert len(seen) == 1
    assert "UNTRUSTED_EVIDENCE" in seen[0]
    assert "TestRun" in seen[0]


def test_run_after_turn_end_audit_evidence_blind_by_default(monkeypatch):
    # Wrapper called without evidence_records → evidence-blind (byte-identical).
    for flag in (
        "MAGI_CUSTOMIZE_LIFECYCLE_TURN_HOOKS_ENABLED",
        "MAGI_CUSTOMIZE_VERIFICATION_ENABLED",
        "MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED",
        "MAGI_EVIDENCE_GROUNDED_CRITIC_ENABLED",
    ):
        monkeypatch.setenv(flag, "1")
    seen: list[str] = []
    policy = CustomizeVerificationPolicy(custom_rules=(_rule(evidence_refs=["TestRun"]),))
    asyncio.run(
        run_after_turn_end_audit(
            final_text="the diff is done",
            model_factory=lambda: object(),
            invoke=_capture_invoke(seen),
            policy_loader=lambda: policy,
        )
    )
    assert len(seen) == 1
    assert "UNTRUSTED_EVIDENCE" not in seen[0]
