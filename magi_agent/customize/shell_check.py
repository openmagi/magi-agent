"""F-EXEC2 — ``shell_check`` custom_rule kind apply helpers.

Operator-authored subprocess VERIFIER. Sibling of
:mod:`magi_agent.customize.shell_command` (action kind) — both kinds share
the :mod:`magi_agent.customize.shell_runner` subprocess spine and the
:class:`magi_agent.adk_bridge.lifecycle_shell_command_control
.LifecycleShellCommandControl` per-(session, turn) budget counter, but the
output contract differs:

* ``shell_command`` returns ``(audit_record, ShellGateVerdict)`` where the
  verdict's ``"block"`` literal is gated by the rule's persisted
  ``action == "block"`` AND ``exit_code > 0``. The audit ledger records
  exit code + stdout/stderr; gating is action-driven.
* ``shell_check`` returns a verdict-shaped audit ``{rule_id, passed, reason,
  status, exit_code, stdout_truncated, stderr_truncated, duration_ms,
  timed_out}`` where ``passed`` is parsed FROM the script's output:

  1. Parse ``stdout`` as JSON. If the result is an object with a boolean
     ``passed`` field, use that; the optional ``reason`` field becomes the
     audit's ``reason``.
  2. Otherwise fall back to exit-code semantics — ``exit_code == 0`` ⇒
     ``passed=True`` with reason ``"shell_check_exit_0"``; non-zero ⇒
     ``passed=False`` with reason ``"shell_check_exit_<N>"``.
  3. Honest-degrade everywhere: ``status="error"`` /
     ``status="budget_exhausted"`` / ``status="timeout"`` records
     ``passed=True`` (a verifier that cannot evaluate must never block a
     turn — the same fail-open contract as :func:`magi_agent.customize
     .criterion_engine.evaluate_criterion`).

The runtime caller (lifecycle_audit fan-out at the matching slot) is
responsible for reducing the per-rule verdicts to a gate verdict via the
existing F-LIFE4a ``_gate_decision_from_audits`` reducer; this helper only
returns the audit record.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


# Sentinels for the parser path so callers can distinguish "exit-code
# fallback" verdicts from "JSON-parsed" verdicts in the audit ledger if
# they ever want to surface that in a dashboard. Kept short to fit the
# 4KB stdout truncation cap with room left for the script's own output.
_REASON_JSON_PARSED = "shell_check_json"
_REASON_EXIT_FALLBACK = "shell_check_exit_{code}"


def _empty_payload(rule: dict[str, Any]) -> dict[str, Any] | None:
    """Return ``rule.what.payload`` as a dict, or ``None`` on shape error."""
    what = rule.get("what")
    if not isinstance(what, dict):
        return None
    payload = what.get("payload")
    if not isinstance(payload, dict):
        return None
    return payload


def _parse_stdout_verdict(stdout: str) -> tuple[bool | None, str | None]:
    """Try parsing ``stdout`` as ``{passed: bool, reason?: str}`` JSON.

    Returns ``(passed, reason)``:

    * ``(bool, str | None)`` when the parse succeeded AND the result is an
      object with a boolean ``passed`` field. The optional ``reason`` field
      is returned verbatim (coerced to ``str``).
    * ``(None, None)`` when the parse failed OR the shape doesn't match
      (caller falls back to exit-code semantics).

    The implementation tolerates trailing whitespace / surrounding noise by
    attempting BOTH a full ``json.loads(stdout)`` AND a salvage pass over
    the last non-blank line (most operator scripts ``echo`` a one-line JSON
    blob after diagnostic output). Honest-degrade: any exception returns
    ``(None, None)`` so the exit-code fallback applies.
    """
    if not isinstance(stdout, str):
        return (None, None)
    text = stdout.strip()
    if not text:
        return (None, None)
    # First pass: full body.
    try:
        parsed: Any = json.loads(text)
    except (ValueError, TypeError):
        parsed = None
    # Second pass: last non-blank line. Many scripts emit diagnostic lines
    # before the final verdict; the last line is the most common single-
    # line JSON output convention. Failure here is silent — we already
    # tried the full body.
    if not isinstance(parsed, dict):
        last_line = next(
            (line for line in reversed(text.splitlines()) if line.strip()),
            "",
        )
        if last_line.strip() and last_line.strip() != text:
            try:
                parsed = json.loads(last_line)
            except (ValueError, TypeError):
                return (None, None)
    if not isinstance(parsed, dict):
        return (None, None)
    raw_passed = parsed.get("passed")
    if not isinstance(raw_passed, bool):
        return (None, None)
    raw_reason = parsed.get("reason")
    reason: str | None
    if isinstance(raw_reason, str):
        reason = raw_reason[:1024]
    elif raw_reason is None:
        reason = None
    else:
        # Coerce non-string reasons to a short repr so downstream consumers
        # never crash on unexpected shapes.
        reason = repr(raw_reason)[:1024]
    return (raw_passed, reason)


async def apply_shell_check_rule(
    rule: dict[str, Any],
    *,
    tool_name: str | None = None,
    stdin_json: dict | None = None,
) -> dict[str, Any]:
    """Execute a single ``shell_check`` rule and return an audit record.

    The returned record's ``passed`` field is the verifier's verdict:

    * Parsed from stdout JSON ``{passed, reason?}`` when available.
    * Falls back to ``exit_code == 0`` otherwise.
    * Always ``True`` on any honest-degrade status (``error`` / ``timeout``
      / ``budget_exhausted`` / ``unsupported_platform``) so a misbehaving
      rule cannot block a turn.

    Fail-open: any exception (payload coerce / runner crash / parse error)
    is captured as an ``error`` record with ``passed=True``.
    """
    rule_id = rule.get("id")
    try:
        payload = _empty_payload(rule)
        if payload is None:
            return {
                "rule_id": rule_id,
                "status": "error",
                "reason": "shell_check rule has no payload",
                "passed": True,
                "exit_code": -1,
                "stdout_truncated": "",
                "stderr_truncated": "",
                "duration_ms": 0,
                "timed_out": False,
            }

        # Lazy import keeps the OFF-path call sites free of the
        # shell_runner module's transitive deps.
        from magi_agent.customize.shell_runner import (  # noqa: PLC0415
            ShellPayload,
            run_shell_payload,
            validate_shell_payload,
        )

        errors = validate_shell_payload(payload)
        if errors:
            return {
                "rule_id": rule_id,
                "status": "error",
                "reason": "; ".join(errors)[:1024],
                "passed": True,
                "exit_code": -1,
                "stdout_truncated": "",
                "stderr_truncated": "",
                "duration_ms": 0,
                "timed_out": False,
            }

        try:
            shell_payload = ShellPayload.model_validate(payload)
        except Exception as exc:  # noqa: BLE001 — fail-open
            return {
                "rule_id": rule_id,
                "status": "error",
                "reason": f"shell payload coerce: {exc!r}"[:1024],
                "passed": True,
                "exit_code": -1,
                "stdout_truncated": "",
                "stderr_truncated": "",
                "duration_ms": 0,
                "timed_out": False,
            }

        result = await run_shell_payload(
            shell_payload, stdin_json=_compose_stdin(stdin_json, tool_name)
        )

        # Map ShellRunResult into the verifier-shaped audit. ``executed``
        # covers any normal exit (including non-zero); ``timeout`` is its
        # own status. ``-1`` / ``-2`` are honest-degrade (runner-internal
        # error / unsupported platform) — surface as ``error`` and force
        # ``passed=True``.
        if result.timed_out:
            return {
                "rule_id": rule_id,
                "status": "timeout",
                "reason": result.reason or "timed_out",
                "passed": True,
                "exit_code": result.exit_code,
                "stdout_truncated": result.stdout,
                "stderr_truncated": result.stderr,
                "duration_ms": result.duration_ms,
                "timed_out": True,
            }
        if result.exit_code in (-1, -2):
            return {
                "rule_id": rule_id,
                "status": "error",
                "reason": result.reason or "runner_error",
                "passed": True,
                "exit_code": result.exit_code,
                "stdout_truncated": result.stdout,
                "stderr_truncated": result.stderr,
                "duration_ms": result.duration_ms,
                "timed_out": False,
            }

        # Parse stdout for the canonical ``{passed, reason?}`` JSON shape.
        # Fall back to exit-code semantics when stdout is not parseable.
        parsed_passed, parsed_reason = _parse_stdout_verdict(result.stdout)
        if parsed_passed is None:
            passed = result.exit_code == 0
            reason = _REASON_EXIT_FALLBACK.format(code=result.exit_code)
        else:
            passed = parsed_passed
            reason = parsed_reason or _REASON_JSON_PARSED

        return {
            "rule_id": rule_id,
            "status": "evaluated",
            "reason": reason,
            "passed": bool(passed),
            "exit_code": result.exit_code,
            "stdout_truncated": result.stdout,
            "stderr_truncated": result.stderr,
            "duration_ms": result.duration_ms,
            "timed_out": False,
        }
    except Exception as exc:  # noqa: BLE001 — fail-open belt + suspenders
        logger.debug("shell_check rule apply failed", exc_info=True)
        return {
            "rule_id": rule_id,
            "status": "error",
            "reason": f"unexpected: {exc!r}"[:1024],
            "passed": True,
            "exit_code": -1,
            "stdout_truncated": "",
            "stderr_truncated": "",
            "duration_ms": 0,
            "timed_out": False,
        }


def _compose_stdin(
    stdin_json: dict | None, tool_name: str | None
) -> dict | None:
    """Compose the stdin JSON sent to the operator script.

    Identical to :func:`magi_agent.customize.shell_command._compose_stdin`
    so a script authored for one kind can be reused as the other without
    re-reading its context source. The runner only writes stdin when the
    composed value is non-None.
    """
    if stdin_json is None and tool_name is None:
        return None
    base = dict(stdin_json) if isinstance(stdin_json, dict) else {}
    if tool_name is not None:
        base.setdefault("tool_name", tool_name)
    return base


def budget_exhausted_record() -> dict[str, Any]:
    """Return a single ``status="budget_exhausted"`` verifier audit record.

    Emitted by callers (lifecycle_audit shell_check fan-outs) when the
    shared per-turn shell budget is depleted BEFORE invoking the runner.
    The record's ``passed=True`` so an exhausted budget never blocks a
    turn (fail-open). Mirrors :func:`magi_agent.customize.shell_command
    .budget_exhausted_record` shape so dashboards can filter both kinds
    of cost-ceiling events with one predicate.
    """
    return {
        "rule_id": None,
        "status": "budget_exhausted",
        "reason": "per-turn shell budget exhausted",
        "passed": True,
        "exit_code": -1,
        "stdout_truncated": "",
        "stderr_truncated": "",
        "duration_ms": 0,
        "timed_out": False,
    }


__all__ = [
    "apply_shell_check_rule",
    "budget_exhausted_record",
]
