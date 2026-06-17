"""Custom verification-rule schema + validation (spec §9.1).

A custom rule (``verification.custom_rules[]`` item):
    {id, scope, enabled, what:{kind, payload}, firesAt, action, projection}

``validate_custom_rule`` returns a list of human-readable errors (empty = valid).
The PUT verb rejects with 400 on any error (no silent drop). This is the full
contract for all three kinds; P1 only *compiles* ``deterministic_ref`` rules into
the gate — ``tool_perm`` (P2) and ``llm_criterion`` (P3/P4) persist but stay inert
until their phase wires them.
"""

from __future__ import annotations

from typing import Any

from magi_agent.customize.what_menu import allowed_actions_for, is_known_ref

CRITERION_MAX = 2000

SCOPES = frozenset({"always", "coding", "research", "delivery", "memory", "task"})
KINDS = frozenset({"deterministic_ref", "tool_perm", "llm_criterion"})
ACTIONS = frozenset({"block", "retry", "ask_approval", "audit", "override"})
FIRES_AT = frozenset({"pre_final", "before_tool_use", "after_tool_use"})

# Allowed least-privilege projection slices (spec §9.1). ``conversation`` (full
# session.events) is intentionally NOT allowed.
_PROJECTION_BASE = frozenset({"result", "args", "scope"})


def _projection_slice_ok(slice_: str) -> bool:
    return slice_ in _PROJECTION_BASE or slice_.startswith("evidence:")


# Legal (kind -> firesAt -> allowed actions) matrix (spec §9.1 table).
_LEGAL: dict[str, dict[str, frozenset[str]]] = {
    "deterministic_ref": {"pre_final": frozenset({"block", "retry", "audit"})},
    "tool_perm": {"before_tool_use": frozenset({"block", "ask_approval"})},
    "llm_criterion": {
        "pre_final": frozenset({"block", "retry", "audit"}),
        "after_tool_use": frozenset({"override"}),
    },
}


def validate_custom_rule(rule: Any) -> list[str]:
    """Return a list of validation errors for a custom rule (empty = valid)."""
    errors: list[str] = []
    if not isinstance(rule, dict):
        return ["rule must be an object"]

    scope = rule.get("scope")
    if scope not in SCOPES:
        errors.append(f"scope must be one of {sorted(SCOPES)}")

    what = rule.get("what")
    if not isinstance(what, dict):
        return [*errors, "what must be an object with kind+payload"]
    kind = what.get("kind")
    payload = what.get("payload")
    if kind not in KINDS:
        return [*errors, f"kind must be one of {sorted(KINDS)}"]
    if not isinstance(payload, dict):
        errors.append("what.payload must be an object")
        payload = {}

    fires_at = rule.get("firesAt")
    action = rule.get("action")
    if fires_at not in FIRES_AT:
        errors.append(f"firesAt must be one of {sorted(FIRES_AT)}")
    if action not in ACTIONS:
        errors.append(f"action must be one of {sorted(ACTIONS)}")

    # (c) legal (kind × firesAt × action) matrix
    legal_for_kind = _LEGAL.get(kind, {})
    if fires_at not in legal_for_kind:
        errors.append(f"kind {kind!r} cannot fire at {fires_at!r}")
    elif action not in legal_for_kind[fires_at]:
        errors.append(
            f"kind {kind!r} at {fires_at!r} allows actions "
            f"{sorted(legal_for_kind[fires_at])}, not {action!r}"
        )

    # (b/d/e/g) kind-specific payload
    if kind == "deterministic_ref":
        ref = payload.get("ref")
        if not isinstance(ref, str) or not is_known_ref(ref):
            errors.append("deterministic_ref.payload.ref must be a known WHAT-menu ref")
        elif isinstance(action, str) and action not in allowed_actions_for(ref):
            errors.append(f"action {action!r} not allowed for ref {ref!r}")
    elif kind == "tool_perm":
        match = payload.get("match")
        if not isinstance(match, dict) or not (
            {"tool", "domain", "domainAllowlist"} & set(match)
        ):
            errors.append(
                "tool_perm.payload.match must specify tool, domain, or domainAllowlist"
            )
        elif "domainAllowlist" in match and (
            not isinstance(match["domainAllowlist"], list)
            or not match["domainAllowlist"]
            or not all(isinstance(d, str) for d in match["domainAllowlist"])
        ):
            errors.append(
                "tool_perm.payload.match.domainAllowlist must be a non-empty string list"
            )
        if payload.get("decision") not in {"deny", "ask"}:
            errors.append("tool_perm.payload.decision must be 'deny' or 'ask'")
    elif kind == "llm_criterion":
        criterion = payload.get("criterion")
        if not isinstance(criterion, str) or not criterion.strip():
            errors.append("llm_criterion.payload.criterion is required")
        elif len(criterion) > CRITERION_MAX:
            errors.append(f"criterion exceeds the {CRITERION_MAX}-char cap")
        if fires_at == "after_tool_use":
            tool_match = payload.get("toolMatch")
            if not isinstance(tool_match, list) or not tool_match:
                errors.append("after_tool_use llm_criterion requires a non-empty toolMatch")

    # (f) projection ⊆ whitelist (conversation rejected)
    projection = rule.get("projection")
    if projection is not None:
        if not isinstance(projection, list):
            errors.append("projection must be a list")
        else:
            bad = [s for s in projection if not (isinstance(s, str) and _projection_slice_ok(s))]
            if bad:
                errors.append(
                    f"projection slices {bad} not allowed (conversation/full history forbidden)"
                )

    return errors
