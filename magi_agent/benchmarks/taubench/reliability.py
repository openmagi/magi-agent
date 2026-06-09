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

    @property
    def any_enabled(self) -> bool:
        return self.arg_validation or self.dup_write_guard or self.verify_before_final


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


__all__ = ["ReliabilityConfig", "validate_args"]
