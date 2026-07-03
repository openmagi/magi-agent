"""Policy-integrity structural checks (the deterministic core of the review loop).

A *policy plan* is what the conversational/guided authoring flow assembles before
persisting: a producer + a gate + the identity binding that links them (see the
policy-abstraction design). This module validates the RELATIONSHIPS between those
parts, catching the silent multi-rule failures a per-rule validator cannot:

- a gate that consumes evidence NO producer in the plan emits (dangling consumer);
- a binding whose producer/gate ids do not match the actual rules;
- an unlock gate bound to a NON-deterministic (result-text / advisory) producer,
  which is the privilege-escalation anti-pattern the security model forbids.

Pure + deterministic (no LLM): the LLM intent-coverage / guarantee-strength layer
of the review loop composes on top of these findings. Per-rule schema validity is
delegated to ``validate_dashboard_check`` (producer) and ``validate_custom_rule``
(gate); this module only checks how they compose.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _get(obj: Any, *keys: str) -> Any:
    """Walk a nested Mapping by keys, returning None on any miss/non-mapping."""
    cur: Any = obj
    for key in keys:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(key)
    return cur


def _gate_require_evidence(gate: Any) -> Mapping[str, Any] | None:
    require = _get(gate, "what", "payload", "requireEvidence")
    return require if isinstance(require, Mapping) else None


def validate_policy_plan(plan: Any) -> list[str]:
    """Return structural findings for a policy plan (empty = structurally sound).

    ``plan`` shape (the authoring flow emits this; the compiler conforms):
        {
          "intent": str,
          "producer": <DashboardCheck draft> | None,   # emits the evidence
          "gate": <tool_perm custom_rule draft with requireEvidence>,
          "binding": {producerRuleId, gateRuleId, evidenceType},
        }
    A gate-only plan (no producer/binding, e.g. a 1-rule policy) is sound as long
    as its gate declares no unsatisfiable requireEvidence.
    """
    if not isinstance(plan, Mapping):
        return ["policy plan must be an object"]

    findings: list[str] = []
    producer = plan.get("producer")
    gate = plan.get("gate")
    binding = plan.get("binding")

    if not isinstance(gate, Mapping):
        return ["policy plan requires a gate rule"]

    require = _gate_require_evidence(gate)

    # A plan with no binding is a plain (possibly 1-rule) policy: only sound if
    # the gate does not declare a requireEvidence that nothing satisfies.
    if binding is None:
        if require is not None:
            findings.append(
                "gate declares requireEvidence but the plan has no producer binding "
                "(dangling consumer: nothing produces the required evidence)"
            )
        return findings

    if not isinstance(binding, Mapping):
        return ["policy plan binding must be an object"]

    b_producer = binding.get("producerRuleId")
    b_gate = binding.get("gateRuleId")
    b_type = binding.get("evidenceType")

    # 1. The gate must actually be an evidence gate when a binding exists.
    if require is None:
        findings.append(
            "binding present but the gate declares no requireEvidence "
            "(the binding links nothing)"
        )
        return findings

    # 2. Identity match: the gate must bind to the SAME producer the plan declares
    #    (the unlock join is by producer identity, not type name).
    r_producer = require.get("producerRuleId")
    if r_producer != b_producer:
        findings.append(
            "identity mismatch: gate.requireEvidence.producerRuleId "
            f"({r_producer!r}) != binding.producerRuleId ({b_producer!r})"
        )

    # 3. Type match across producer, gate, and binding (a gate reading a type
    #    nobody emits is the classic dangling consumer).
    r_type = require.get("evidenceType")
    if r_type != b_type:
        findings.append(
            "type mismatch: gate.requireEvidence.evidenceType "
            f"({r_type!r}) != binding.evidenceType ({b_type!r})"
        )

    # 4. binding.gateRuleId must name the gate rule.
    gate_id = gate.get("id")
    if b_gate is not None and gate_id is not None and b_gate != gate_id:
        findings.append(
            f"binding.gateRuleId ({b_gate!r}) does not match the gate rule id ({gate_id!r})"
        )

    # 5. The producer must exist, emit binding.evidenceType, and be DETERMINISTIC
    #    (arguments-based domain allowlist) — a result-text / advisory producer is
    #    NOT unlock-eligible (privilege-escalation anti-pattern).
    if not isinstance(producer, Mapping):
        findings.append(
            f"binding.producerRuleId ({b_producer!r}) has no producer in the plan "
            "(dangling consumer: the gate would never be satisfiable)"
        )
    else:
        p_id = producer.get("id")
        if b_producer is not None and p_id is not None and b_producer != p_id:
            findings.append(
                f"binding.producerRuleId ({b_producer!r}) does not match the "
                f"producer id ({p_id!r})"
            )
        p_type = producer.get("emitsEvidenceType")
        if p_type != b_type:
            findings.append(
                "type mismatch: producer.emitsEvidenceType "
                f"({p_type!r}) != binding.evidenceType ({b_type!r})"
            )
        if isinstance(p_type, str) and not p_type.startswith("custom:"):
            findings.append(
                "producer.emitsEvidenceType must be an operator-named custom: type"
            )
        has_domain_allowlist = bool(_get(producer, "trigger", "domainAllowlist"))
        if not has_domain_allowlist:
            findings.append(
                "unlock producer must be deterministic (an arguments-based "
                "domainAllowlist trigger); a result-text / advisory producer is "
                "not unlock-eligible"
            )

    # 6. Scope: the session unlock gate must use session scope (an absent scope
    #    defaults to session in the runtime; a non-session scope is a mismatch).
    r_scope = require.get("scope")
    if r_scope is not None and r_scope != "session":
        findings.append(
            f"requireEvidence.scope must be 'session' for a session unlock gate (got {r_scope!r})"
        )

    return findings


def policy_plan_is_sound(plan: Any) -> bool:
    """True when the plan has no structural findings."""
    return not validate_policy_plan(plan)
