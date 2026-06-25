"""F-EXEC1 — ``shell_command`` custom_rule kind apply helpers.

Third action-kind in the customize wizard surface (after F-MUT1
``prompt_injection`` and F-MUT2 ``output_rewrite``). Exposes the
:mod:`magi_agent.customize.shell_runner` subprocess runner as a constrained
operator-authored kind. A rule's payload conforms to
:class:`magi_agent.customize.shell_runner.ShellPayload`; at runtime the
applier resolves the payload, invokes :func:`run_shell_payload`, and converts
the result into an audit-ledger-shaped record plus an optional gate verdict.

Two consumer sites for v1 (see :func:`magi_agent.facades.execute_tool_with_hooks`):

* ``before_tool_use`` — fired BEFORE the dispatcher dispatches the tool. When
  the rule's persisted ``action == "block"`` AND the subprocess exited with a
  non-zero exit code (and was not a synthetic ``-1`` ``internal_error`` /
  ``-2`` ``unsupported_platform``), the helper returns a ``block`` verdict so
  the caller can short-circuit dispatch with a blocked :class:`ToolResult`.
* ``after_tool_use`` — fired AFTER the dispatcher has returned. Always
  audit-only: by the time the runtime observes the result, the tool already
  ran, so an after-fact "block" has no honest semantics. Persisted
  ``action == "block"`` rules at this slot still execute but the verdict is
  recorded as audit only.

Per-turn budget: the runner itself is stateless; the per-(session, turn)
budget cap is enforced upstream by
:class:`magi_agent.adk_bridge.lifecycle_shell_command_control.LifecycleShellCommandControl`
which decrements a shared counter on every spawn. The helpers here accept an
optional ``remaining_budget`` integer; when zero or negative they short-circuit
to a single ``budget_exhausted`` audit record without invoking the runner.

Fail-open contract: the runner already maps any internal exception to
``ShellRunResult(exit_code=-1, reason="internal_error")``. The helpers here
add one more layer of defense: any unexpected exception (validator failure,
runner import error, etc.) is captured as an audit record with
``status="error"``. The runtime path NEVER raises out of these helpers, so a
buggy rule cannot wedge a turn.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# Verdict literal returned alongside the audit record so the caller can
# short-circuit when the rule's persisted ``action == "block"`` AND the
# subprocess gave a non-zero exit. "proceed" otherwise (including OFF path,
# missing rule, ``audit`` action, internal error, etc.). Mirrors the
# lifecycle_audit GateVerdict alphabet ("proceed" / "block" / "ask"); v1
# never returns "ask" — operator-authored shell hooks have no honest
# approval surface yet.
ShellGateVerdict = str  # "proceed" | "block"


def _empty_payload(rule: dict[str, Any]) -> dict[str, Any] | None:
    """Return ``rule.what.payload`` as a dict, or ``None`` on shape error."""
    what = rule.get("what")
    if not isinstance(what, dict):
        return None
    payload = what.get("payload")
    if not isinstance(payload, dict):
        return None
    return payload


async def apply_shell_command_rule(
    rule: dict[str, Any],
    *,
    tool_name: str | None = None,
    stdin_json: dict | None = None,
    honor_block_action: bool = True,
) -> tuple[dict[str, Any], ShellGateVerdict]:
    """Execute a single ``shell_command`` rule and return (audit_record, verdict).

    ``audit_record`` fields (caller may forward to the audit ledger):
        rule_id, exit_code, stdout_truncated, stderr_truncated, duration_ms,
        timed_out, reason, status

    ``status`` ∈ {``"executed"``, ``"timeout"``, ``"error"``,
    ``"budget_exhausted"``, ``"skipped"``}. ``"skipped"`` is reserved for the
    caller's pre-gate (e.g. rule disabled / firesAt mismatch); this helper
    only emits ``"executed"`` / ``"timeout"`` / ``"error"`` directly.

    ``verdict``:
        ``"block"`` when ``honor_block_action`` is True AND the persisted
        ``rule.action == "block"`` AND ``exit_code > 0``. ``"proceed"``
        otherwise (including the ``-1`` / ``-2`` runner-internal exit codes,
        which fail-open per the runner's contract).

    Fail-open: any exception (validator failure, runner import failure) is
    captured as ``status="error"``, ``passed=True`` so a buggy rule never
    blocks a turn.
    """
    rule_id = rule.get("id")
    try:
        payload = _empty_payload(rule)
        if payload is None:
            return (
                {
                    "rule_id": rule_id,
                    "status": "error",
                    "reason": "shell_command rule has no payload",
                    "passed": True,
                    "exit_code": -1,
                    "stdout_truncated": "",
                    "stderr_truncated": "",
                    "duration_ms": 0,
                    "timed_out": False,
                },
                "proceed",
            )

        # Lazy import keeps the OFF-path facades hot path free of the
        # shell_runner module's transitive deps (pydantic ValidationError
        # import + asyncio.subprocess).
        from magi_agent.customize.shell_runner import (  # noqa: PLC0415
            ShellPayload,
            run_shell_payload,
            validate_shell_payload,
        )

        errors = validate_shell_payload(payload)
        if errors:
            return (
                {
                    "rule_id": rule_id,
                    "status": "error",
                    "reason": "; ".join(errors)[:1024],
                    "passed": True,
                    "exit_code": -1,
                    "stdout_truncated": "",
                    "stderr_truncated": "",
                    "duration_ms": 0,
                    "timed_out": False,
                },
                "proceed",
            )

        try:
            shell_payload = ShellPayload.model_validate(payload)
        except Exception as exc:  # noqa: BLE001 — fail-open
            return (
                {
                    "rule_id": rule_id,
                    "status": "error",
                    "reason": f"shell payload coerce: {exc!r}"[:1024],
                    "passed": True,
                    "exit_code": -1,
                    "stdout_truncated": "",
                    "stderr_truncated": "",
                    "duration_ms": 0,
                    "timed_out": False,
                },
                "proceed",
            )

        result = await run_shell_payload(
            shell_payload, stdin_json=_compose_stdin(stdin_json, tool_name)
        )

        # Map ShellRunResult into audit-ledger shape. ``executed`` covers
        # any normal exit (including non-zero exit codes from the script
        # itself); ``timeout`` is its own status for ledger filtering.
        if result.timed_out:
            status = "timeout"
        elif result.exit_code in (-1, -2):
            # -1: runner-internal exception / timeout fallback (already covered
            # by ``timed_out``); -2: unsupported_platform (Windows). Both are
            # honest-degrade — surface as ``error`` so the audit ledger filter
            # distinguishes script-side failure (status=executed exit_code!=0)
            # from runner-side failure.
            status = "error"
        else:
            status = "executed"

        audit = {
            "rule_id": rule_id,
            "status": status,
            "reason": result.reason or "",
            "passed": status == "executed" and result.exit_code == 0,
            "exit_code": result.exit_code,
            "stdout_truncated": result.stdout,
            "stderr_truncated": result.stderr,
            "duration_ms": result.duration_ms,
            "timed_out": result.timed_out,
        }

        verdict: ShellGateVerdict = "proceed"
        if (
            honor_block_action
            and rule.get("action") == "block"
            and status == "executed"
            and result.exit_code > 0
        ):
            verdict = "block"

        return audit, verdict
    except Exception as exc:  # noqa: BLE001 — fail-open belt + suspenders
        logger.debug("shell_command rule apply failed", exc_info=True)
        return (
            {
                "rule_id": rule_id,
                "status": "error",
                "reason": f"unexpected: {exc!r}"[:1024],
                "passed": True,
                "exit_code": -1,
                "stdout_truncated": "",
                "stderr_truncated": "",
                "duration_ms": 0,
                "timed_out": False,
            },
            "proceed",
        )


def _compose_stdin(
    stdin_json: dict | None, tool_name: str | None
) -> dict | None:
    """Compose the stdin JSON sent to the operator script.

    The runner only writes stdin when ``stdin_json`` is non-None. We merge
    the caller-provided context (tool args / output / lifecycle slot) with
    the ``tool_name`` key so the script can branch on the tool name without
    re-reading it from environment / args.
    """
    if stdin_json is None and tool_name is None:
        return None
    base = dict(stdin_json) if isinstance(stdin_json, dict) else {}
    if tool_name is not None:
        base.setdefault("tool_name", tool_name)
    return base


def budget_exhausted_record() -> dict[str, Any]:
    """Return a single ``status="budget_exhausted"`` audit record.

    Emitted by callers (facades / lifecycle_audit) when the per-turn budget
    is depleted BEFORE invoking the runner. Mirrors the F-LIFE2
    budget_exhausted ledger shape so dashboards can filter both kinds of
    cost-ceiling events with one predicate.
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
    "ShellGateVerdict",
    "apply_shell_command_rule",
    "budget_exhausted_record",
]
