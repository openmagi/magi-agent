# magi_agent/benchmarks/taubench/reliability.py
"""Pure, network-free reliability levers for the τ-bench driver boundary.

No tau_bench import, no ADK import. Three general levers:
- L1 validate_args: schema-driven argument validation before a tool runs.
- L3 WriteLedger / dup guard: block re-executing an identical successful write.
- L2 verify_final: ground a success claim against recorded write outcomes.
"""
from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict


class ReliabilityConfig(BaseModel):
    """Toggle for each lever. All default OFF (behavior-preserving)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    arg_validation: bool = False
    dup_write_guard: bool = False
    verify_before_final: bool = False
    completion_review: bool = False

    @property
    def any_enabled(self) -> bool:
        return (
            self.arg_validation
            or self.dup_write_guard
            or self.verify_before_final
            or self.completion_review
        )


_JSON_TYPE_CHECKS: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": (list, tuple),
}


def _type_ok(value: object, expected: str) -> bool:
    py = _JSON_TYPE_CHECKS.get(expected)
    if py is None:
        return True  # unknown type spec -> do not reject
    if expected in ("integer", "number") and isinstance(value, bool):
        return False  # bool is a subclass of int; reject it for numeric fields
    return isinstance(value, py)


def validate_args(parameters: dict, arguments: dict) -> str | None:
    """Validate `arguments` against a tau_bench tool spec's `parameters` schema.

    Returns a corrective message string on a CLEAR violation (missing required
    key, enum mismatch, wrong primitive type), else None. Conservative: unknown
    keys and unconstrained values pass so the lever never false-blocks a
    plausible call.
    """
    if not isinstance(parameters, dict):
        return None
    props = parameters.get("properties")
    if not isinstance(props, dict):
        return None
    args = arguments or {}
    required = parameters.get("required") or []
    missing = [k for k in required if k not in args]
    if missing:
        return (
            f"Invalid arguments: missing required parameter(s) {missing}. "
            "Supply them and call the tool again."
        )
    problems: list[str] = []
    for key, value in args.items():
        spec = props.get(key)
        if not isinstance(spec, dict):
            continue  # unknown-but-plausible key: do not reject
        enum = spec.get("enum")
        if isinstance(enum, list) and enum and value not in enum:
            problems.append(f"{key}={value!r} is not one of {enum}")
            continue
        expected = spec.get("type")
        if isinstance(expected, str) and not _type_ok(value, expected):
            problems.append(f"{key}={value!r} is not of type {expected}")
    if problems:
        return (
            "Invalid arguments: " + "; ".join(problems)
            + ". Correct them and call the tool again."
        )
    return None


DEFAULT_WRITE_PREFIXES = ("book_", "cancel_", "update_", "send_")


class WriteLedger:
    """Per-episode record of write-tool calls and their outcomes.

    A "write" is any tool whose name starts with a configured prefix. A "repeat"
    write is an identical (name, normalized-args) write that already succeeded.
    """

    def __init__(self, write_prefixes: tuple[str, ...] = DEFAULT_WRITE_PREFIXES) -> None:
        self._prefixes = tuple(write_prefixes)
        self._records: list[tuple[str, str, bool]] = []  # (name, args_key, ok)

    def is_write(self, tool_name: str) -> bool:
        return any(tool_name.startswith(p) for p in self._prefixes)

    @staticmethod
    def _key(arguments: dict) -> str:
        try:
            return json.dumps(arguments or {}, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return repr(arguments)

    def record(self, tool_name: str, arguments: dict, *, ok: bool) -> None:
        self._records.append((tool_name, self._key(arguments), ok))

    def is_repeat_write(self, tool_name: str, arguments: dict) -> bool:
        key = self._key(arguments)
        return any(
            name == tool_name and arg_key == key and ok
            for (name, arg_key, ok) in self._records
        )

    def had_successful_write(self) -> bool:
        return any(ok for (_name, _key, ok) in self._records)

    def last_write_errored(self) -> bool:
        if not self._records:
            return False
        return not self._records[-1][2]


def looks_like_error(observation: object) -> bool:
    """True if an env observation string indicates a tool error."""
    return isinstance(observation, str) and observation.strip().lower().startswith("error")


# Assertion-style success phrases only. Deliberately excludes bare "confirm"/
# "completed" so a clarifying question ("Can you confirm your dates?") is not
# mistaken for a success claim.
_SUCCESS_MARKERS = (
    "is booked",
    "has been booked",
    "reservation id",
    "confirmation number",
    "is confirmed",
    "booking is confirmed",
    "successfully",
    "has been cancelled",
    "has been canceled",
    "has been completed",
)


def verify_final(ledger: WriteLedger, agent_text: str) -> str | None:
    """If the agent asserts success but the ledger does not support it (last
    write errored, or no successful write at all), return a one-time corrective
    message; else None. The caller enforces the one-shot-per-episode bound.
    """
    text = (agent_text or "").lower()
    if not any(marker in text for marker in _SUCCESS_MARKERS):
        return None
    if ledger.last_write_errored() or not ledger.had_successful_write():
        return (
            "Before confirming success to the user: your records show no "
            "successful write operation (the last write either failed or never "
            "happened). Re-check the tool results, then either perform the "
            "required action correctly or tell the user it did not complete. "
            "Do not claim success the tool results do not support."
        )
    return None


# Conclusion = the agent is wrapping up: either a success claim (reuse the L2
# success markers) OR a refusal/closure. Catches both under-action (refusal that
# leaves work undone) and premature success claims. Lowercased substring match.
_CONCLUSION_MARKERS = _SUCCESS_MARKERS + (
    "unable to",
    "not able to",
    "cannot",
    "can't",
    "won't be able",
    "i'm sorry",
    "i am sorry",
    "unfortunately",
    "is there anything else",
    "anything else i can",
)


def is_conclusion(agent_text: str) -> bool:
    """True if the agent text reads like it is concluding the interaction (a
    success claim or a refusal/closure), as opposed to asking for more info."""
    text = (agent_text or "").lower()
    return any(marker in text for marker in _CONCLUSION_MARKERS)


def completion_review_nudge() -> str:
    """A domain-agnostic completion+scope self-review prompt. No ground-truth, no
    domain rules — a general 'did I do all and only what was asked?' check."""
    return (
        "Before you confirm completion or close this out: re-read the user's "
        "messages and list every concrete action they asked you to perform. For "
        "each, state whether you actually executed it (and with which tool call) "
        "or not. Then check whether you performed any action the user did NOT "
        "request. If a requested action is missing, perform it now. If you "
        "performed an unrequested action, correct it. Only confirm completion "
        "once every requested action — and only those — has been done. Do not "
        "claim completion you cannot support."
    )


__all__ = [
    "DEFAULT_WRITE_PREFIXES",
    "ReliabilityConfig",
    "WriteLedger",
    "completion_review_nudge",
    "is_conclusion",
    "looks_like_error",
    "validate_args",
    "verify_final",
]
