"""Policy-plan structural integrity (the deterministic core of the review loop)."""
from __future__ import annotations

from magi_agent.customize.policy_plan import policy_plan_is_sound, validate_policy_plan


def _producer(**over) -> dict:
    base = {
        "id": "credible-source",
        "label": "credible source",
        "scope": "always",
        "enabled": True,
        "trigger": {"tool": "web_fetch", "domainAllowlist": ["sec.gov"]},
        "action": "audit",
        "emitsEvidenceType": "custom:SourceCredibility",
    }
    base.update(over)
    return base


def _gate(**require_over) -> dict:
    require = {
        "evidenceType": "custom:SourceCredibility",
        "producerRuleId": "credible-source",
    }
    require.update(require_over)
    return {
        "id": "cr_gate",
        "scope": "always",
        "enabled": True,
        "what": {
            "kind": "tool_perm",
            "payload": {
                "match": {"tool": "dangerous_tool"},
                "decision": "deny",
                "requireEvidence": require,
            },
        },
        "firesAt": "before_tool_use",
        "action": "block",
    }


def _plan(**over) -> dict:
    base = {
        "intent": "require a credible source before a high-risk tool",
        "producer": _producer(),
        "gate": _gate(),
        "binding": {
            "producerRuleId": "credible-source",
            "gateRuleId": "cr_gate",
            "evidenceType": "custom:SourceCredibility",
        },
    }
    base.update(over)
    return base


# --- sound plans ---


def test_well_formed_plan_is_sound() -> None:
    assert validate_policy_plan(_plan()) == []
    assert policy_plan_is_sound(_plan())


def test_gate_only_plan_without_require_evidence_is_sound() -> None:
    # A plain 1-rule policy (gate with no requireEvidence, no binding).
    gate = {
        "id": "cr_plain",
        "what": {"kind": "tool_perm", "payload": {"match": {"tool": "x"}, "decision": "deny"}},
    }
    assert validate_policy_plan({"intent": "block x", "gate": gate, "binding": None}) == []


# --- structural failures the review loop must catch ---


def test_identity_mismatch_flagged() -> None:
    plan = _plan(gate=_gate(producerRuleId="cr_other"))
    findings = validate_policy_plan(plan)
    assert any("identity mismatch" in f for f in findings)


def test_type_mismatch_flagged() -> None:
    plan = _plan(gate=_gate(evidenceType="custom:Other"))
    findings = validate_policy_plan(plan)
    assert any("type mismatch" in f for f in findings)


def test_dangling_consumer_no_producer_flagged() -> None:
    plan = _plan(producer=None)
    findings = validate_policy_plan(plan)
    assert any("dangling consumer" in f or "has no producer" in f for f in findings)


def test_binding_present_but_gate_has_no_require_evidence() -> None:
    gate = {
        "id": "cr_gate",
        "what": {"kind": "tool_perm", "payload": {"match": {"tool": "x"}, "decision": "deny"}},
    }
    findings = validate_policy_plan(_plan(gate=gate))
    assert any("declares no requireEvidence" in f for f in findings)


def test_gate_require_evidence_without_binding_flagged() -> None:
    # A gate requiring evidence but no producer binding -> dangling.
    findings = validate_policy_plan({"intent": "x", "gate": _gate(), "binding": None})
    assert any("dangling consumer" in f for f in findings)


def test_non_deterministic_producer_rejected() -> None:
    # A result-text producer (no domainAllowlist) bound to an unlock gate is the
    # anti-pattern: not unlock-eligible.
    # Replacing trigger drops the domainAllowlist (result-text match only).
    producer = _producer(trigger={"tool": "web_fetch", "match": {"pattern": "official"}})
    findings = validate_policy_plan(_plan(producer=producer))
    assert any("deterministic" in f for f in findings)


def test_producer_emits_wrong_type_flagged() -> None:
    plan = _plan(producer=_producer(emitsEvidenceType="custom:Different"))
    findings = validate_policy_plan(plan)
    assert any("type mismatch" in f for f in findings)


def test_producer_non_custom_type_flagged() -> None:
    # emitsEvidenceType must be custom: (matches the 2b hardening); a builtin-like
    # name is flagged (both as a mismatch and a non-custom producer type).
    plan = _plan(
        producer=_producer(emitsEvidenceType="TestRun"),
        gate=_gate(evidenceType="TestRun"),
        binding={
            "producerRuleId": "credible-source",
            "gateRuleId": "cr_gate",
            "evidenceType": "TestRun",
        },
    )
    findings = validate_policy_plan(plan)
    assert any("custom:" in f for f in findings)


def test_gate_id_binding_mismatch_flagged() -> None:
    plan = _plan(binding={
        "producerRuleId": "credible-source",
        "gateRuleId": "cr_WRONG",
        "evidenceType": "custom:SourceCredibility",
    })
    findings = validate_policy_plan(plan)
    assert any("gateRuleId" in f for f in findings)


def test_bad_scope_flagged() -> None:
    plan = _plan(gate=_gate(scope="turn"))
    findings = validate_policy_plan(plan)
    assert any("scope" in f for f in findings)


# --- defensive ---


def test_non_object_plan() -> None:
    assert validate_policy_plan("nope") == ["policy plan must be an object"]


def test_missing_gate() -> None:
    assert validate_policy_plan({"intent": "x"}) == ["policy plan requires a gate rule"]
