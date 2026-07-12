"""Per-turn invariant engine (I1..I9).

These hold for EVERY TurnResult of EVERY scenario in EVERY tier. Each is
recomputed by the harness from first principles using the PRODUCTION validators
and vocabulary sets (they are pure), so the invariants are immune to prose
variation and can never silently drift from the runtime. A violation is a hard
failure carrying ``{invariant_id, turn_index, evidence}``.

See design section 6.4. Where a magi-agent quirk weakens an invariant (flow B
LLM questions carry an empty ``targets_field``), the invariant degrades
honestly rather than being papered over.

NOTE (review fold): I8 (no store write before the save step) is NOT a per-turn
check in this module - it is the store-level oracle
``oracles.persisted.assert_store_untouched``, because "the store did not
change" is a property of bytes on disk across the whole pre-save window, not
of a single TurnResult. This module therefore implements I1-I7 and I9.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from benchmarks.authoring.adapter import TurnResult

# Production vocabulary + validators (pure; imported so the oracle can never
# drift from the runtime's own definitions).
from magi_agent.customize.custom_rules import validate_custom_rule
from magi_agent.customize.nl_compiler_interactive import (
    ACTIONS,
    FIRES_AT,
    KINDS,
    SCOPES,
    _to_plain_language,
)
from magi_agent.customize.policy_plan import validate_policy_plan

_DRAFT_ALLOWED_KEYS = frozenset(
    {"id", "scope", "enabled", "firesAt", "action", "what", "description",
     "projection", "_payload_hint"}
)
_PARAM_KEYS = frozenset(
    {"gatedTool", "fetchTool", "allowlistDomains", "evidenceLabel", "onUnavailable"}
)

#: answer-id -> (dotted draft path, allowed-vocabulary set or None for free text)
_ANSWER_TO_DRAFT: dict[str, tuple[str, frozenset[str] | None]] = {
    "q_what.kind": ("what.kind", frozenset(KINDS)),
    "q_firesAt": ("firesAt", frozenset(FIRES_AT)),
    "q_action": ("action", frozenset(ACTIONS)),
    "q_scope": ("scope", frozenset(SCOPES)),
    # q_what.payload -> free-text buffer; the exact value is not required to
    # land verbatim (it is compiled by the LLM next turn), so I4 does not
    # assert it.
}

_FLOW_A_KEYS = {
    "assistant_message", "draft", "missing_fields", "questions", "needs_more",
    "ready_to_save", "schema_issues",
}
_FLOW_B_KEYS = {
    "assistant_message", "params", "plan", "missing_params", "questions",
    "needs_more", "ready_to_save", "schema_issues", "producer_reused",
}


@dataclass(frozen=True)
class InvariantViolation:
    invariant_id: str
    turn_index: int
    evidence: str


def _get_path(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover - debug only
        return "<missing>"


_MISSING = _Missing()


def _is_error_envelope(raw: dict[str, Any]) -> bool:
    """A documented fail-soft error envelope (HTTP 200 with an ``error`` key).

    The server emits THREE shapes for a fail-soft compile error/timeout
    (transport/customize.py): route A ``{"ok": False, "error": ..., "draft": None}``,
    route B ``{"ok": False, "error": ..., "plan": None}``, and the flow-B
    interactive compile-policy route ``{"ready_to_save": False, "error": ...}``
    (customize.py:858-866) which carries NO ``ok`` key. Keying on ``ok is False``
    alone missed the third shape and misclassified an honest ``compile timed out``
    as an I1 shape violation (missing response keys). The server contract is: the
    presence of a top-level ``error`` string IS the error envelope. I9 (error
    honesty) separately catches an error envelope that dishonestly claims
    ``ready_to_save: true``, so widening the detector does not weaken honesty.
    """
    if not isinstance(raw, dict) or "error" not in raw:
        return False
    # ok:False is one signal; absence of ok with a top-level error is another.
    return raw.get("ok") is not True


def check_invariants(
    result: TurnResult,
    *,
    flow: str,
    answers: dict[str, str] | None = None,
    turn_index: int = 0,
) -> list[InvariantViolation]:
    """Recompute I1..I9 for one turn. Returns every violation found."""
    answers = answers or {}
    v: list[InvariantViolation] = []

    def add(inv: str, evidence: str) -> None:
        v.append(InvariantViolation(inv, turn_index, evidence))

    raw = result.raw if isinstance(result.raw, dict) else {}
    error_envelope = _is_error_envelope(raw)

    # --- I1 shape ---
    if error_envelope:
        pass  # documented fail-soft envelope
    elif result.http_status != 200:
        add("I1", f"http_status={result.http_status} and not an error envelope")
    else:
        expected = _FLOW_A_KEYS if flow == "single_rule" else _FLOW_B_KEYS
        missing_keys = expected - set(raw.keys())
        if missing_keys:
            add("I1", f"missing response keys: {sorted(missing_keys)}")

    # Skip the working-state invariants on an error envelope (no working state).
    if error_envelope:
        # --- I9 error honesty ---
        if result.ready_to_save:
            add("I9", "error envelope reported ready_to_save=true")
        return v

    working = result.working if isinstance(result.working, dict) else {}

    # --- I2 ready-truth ---
    if flow == "single_rule":
        try:
            validator_clean = not validate_custom_rule(working)
        except Exception as exc:  # noqa: BLE001
            validator_clean = False
            add("I2", f"validate_custom_rule raised: {exc}")
        expected_ready = (not result.missing) and validator_clean
        if result.ready_to_save != expected_ready:
            add(
                "I2",
                f"ready_to_save={result.ready_to_save} but "
                f"(missing_empty={not result.missing}, "
                f"validator_clean={validator_clean})",
            )
    else:  # linked_policy
        plan = result.plan
        # Review fold (Wave A): also re-run the EMBEDDED validators, not only
        # the composition check. validate_policy_plan delegates per-rule
        # schema validity to the producer/gate validators; leaning on
        # production's own "plan is not None implies embedded-clean" gating
        # would let a future regression (non-None plan carrying an invalid
        # embedded rule, ready still set) slip past. The harness must stay an
        # INDEPENDENT oracle.
        embedded_clean = False
        if isinstance(plan, dict):
            from magi_agent.packs.dashboard_authored import (
                validate_dashboard_check,
            )

            producer = plan.get("producer")
            gate = plan.get("gate")
            try:
                embedded_clean = (
                    isinstance(producer, dict)
                    and isinstance(gate, dict)
                    and not validate_dashboard_check(producer)
                    and not validate_custom_rule(gate)
                )
            except Exception as exc:  # noqa: BLE001
                add("I2", f"embedded validator raised: {exc}")
        plan_clean = (
            plan is not None and embedded_clean and not validate_policy_plan(plan)
        )
        expected_ready = plan_clean
        if result.ready_to_save != expected_ready:
            add(
                "I2",
                f"ready_to_save={result.ready_to_save} but plan_clean={plan_clean}",
            )

    # --- I3 question discipline ---
    questions = result.questions or []
    if len(questions) > 2:
        add("I3", f"{len(questions)} questions > 2")
    if flow == "single_rule":
        missing_set = set(result.missing or [])
        for q in questions:
            tf = q.get("targets_field")
            if tf and tf not in missing_set:
                add("I3", f"question targets_field={tf!r} not in missing {sorted(missing_set)}")
    # flow B: targets_field is empty for LLM questions -> count cap only (R2).

    # --- I4 operator supremacy ---
    for ans_id, ans_val in answers.items():
        ans_val = (ans_val or "").strip()
        if not ans_val:
            continue
        if flow == "single_rule":
            mapping = _ANSWER_TO_DRAFT.get(ans_id)
            if mapping is None:
                continue  # q_what.payload / unknown id: not asserted
            path, vocab = mapping
            landed = _get_path(working, path)
            valid = vocab is None or ans_val in vocab
            if valid:
                if landed is _MISSING or landed != ans_val:
                    add("I4", f"answer {ans_id}={ans_val!r} did not land at {path} (got {landed!r})")
            else:
                # invalid answer must be ABSENT, not coerced
                if landed is not _MISSING and landed == ans_val:
                    add("I4", f"invalid answer {ans_id}={ans_val!r} was coerced into {path}")
        else:  # linked_policy: answer keys ARE param names
            if ans_id in _PARAM_KEYS:
                landed = working.get(ans_id, _MISSING)
                # allowlistDomains normalizes comma strings to a list; only
                # assert the simple scalar params land verbatim.
                if ans_id in ("gatedTool", "fetchTool", "evidenceLabel"):
                    if landed is _MISSING or landed != ans_val:
                        add("I4", f"param answer {ans_id}={ans_val!r} did not land (got {landed!r})")

    # --- I5 vocabulary containment (fixed point of the production scrubber) ---
    rendered: list[str] = []
    if isinstance(result.assistant_message, str):
        rendered.append(result.assistant_message)
    for q in questions:
        prompt = q.get("prompt")
        if isinstance(prompt, str):
            rendered.append(prompt)
    for s in result.schema_issues or []:
        if isinstance(s, str):
            rendered.append(s)
    for s in rendered:
        if _to_plain_language(s) != s:
            add("I5", f"vocabulary leak (scrubber not a fixed point): {s!r}")

    # --- I6 working-state hygiene ---
    if flow == "single_rule":
        unknown = set(working.keys()) - _DRAFT_ALLOWED_KEYS
        if unknown:
            add("I6", f"draft has non-allowlisted keys: {sorted(unknown)}")
    else:
        unknown = set(working.keys()) - _PARAM_KEYS
        if unknown:
            add("I6", f"params has non-allowlisted keys: {sorted(unknown)}")
        if working.get("onUnavailable") == "allow":
            add("I6", "onUnavailable == 'allow' (unrepresentable)")

    # --- I7 consistency ---
    expected_needs_more = bool(result.missing or result.schema_issues)
    if bool(result.needs_more) != expected_needs_more:
        add(
            "I7",
            f"needs_more={result.needs_more} but "
            f"(missing or schema_issues)={expected_needs_more}",
        )
    if flow == "linked_policy":
        plan = result.plan
        if (plan is None) == bool(result.ready_to_save):
            add("I7", f"plan is None ({plan is None}) must equal not-ready ({not result.ready_to_save})")
        if plan is not None:
            binding = plan.get("binding", {}) if isinstance(plan, dict) else {}
            producer = plan.get("producer", {}) if isinstance(plan, dict) else {}
            gate = plan.get("gate", {}) if isinstance(plan, dict) else {}
            if binding.get("producerRuleId") != producer.get("id"):
                add("I7", "binding.producerRuleId != producer.id")
            if binding.get("gateRuleId") != gate.get("id"):
                add("I7", "binding.gateRuleId != gate.id")

    # --- I9 error honesty (non-error envelope path: nothing to add) ---
    return v
