"""Customize after-tool-use ingestion gate (P4).

A ``LoopControl`` attached to the control plane's ``on_after_tool`` fan-out that
overrides (strips) a tool's result when an enabled ``after_tool_use`` custom rule
fires — a content-aware ingestion gate (e.g. "block a web_search result that
isn't a 10-K filing").

Each rule (kind ``llm_criterion``, firesAt ``after_tool_use``, action
``override``) supports two modes, combinable:

* **Deterministic ``contentMatch`` pre-filter** — substring/regex on the result
  text. Cheap, model-free. When present without a ``criterion`` it IS the gate:
  a match (honoring ``negate``) overrides the result.
* **LLM ``criterion``** — reuses :func:`customize.criterion_engine.evaluate_criterion`.
  When a ``contentMatch`` is also present it acts as a pre-filter so the (costly)
  LLM only runs on matching results; a fail verdict overrides.

Flag-gated: inert unless ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` +
``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED``. The LLM sub-mode additionally needs a
criterion model factory (``None`` when ``MAGI_EGRESS_GATE_ENABLED`` is off → such
rules fall back to their ``contentMatch`` or stay inert).

Fail-OPEN everywhere: any error (or absent model) returns ``None`` (no override)
so a bad rule / flaky model can never wedge tool ingestion.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from magi_agent.adk_bridge.control_plane import BaseLoopControl
from magi_agent.customize.criterion_engine import evaluate_criterion

CUSTOMIZE_AFTER_TOOL_CONTROL_NAME = "magi_customize_after_tool"
CUSTOMIZE_AFTER_TOOL_BLOCK_TYPE = "MAGI_CUSTOMIZE_AFTER_TOOL_BLOCKED"

InvokeFn = Callable[[Any, str], Awaitable[str]]


def _tool_name(tool: Any) -> str:
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else ""


def _result_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(result)


def _content_matches(content_match: dict[str, Any], text: str) -> bool:
    pattern = content_match.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return False
    negate = bool(content_match.get("negate"))
    if content_match.get("isRegex"):
        try:
            hit = re.search(pattern, text) is not None
        except re.error:
            return False
    else:
        hit = pattern in text
    return (not hit) if negate else hit


def _default_policy_loader() -> Any:
    from magi_agent.customize.store import load_overrides
    from magi_agent.customize.verification_policy import CustomizeVerificationPolicy

    return CustomizeVerificationPolicy.from_overrides(load_overrides())


class CustomizeAfterToolControl(BaseLoopControl):
    """After-tool ingestion gate over enabled ``after_tool_use`` custom rules."""

    name = CUSTOMIZE_AFTER_TOOL_CONTROL_NAME

    def __init__(
        self,
        *,
        model_factory: Callable[[], Any] | None = None,
        policy_loader: Callable[[], Any] | None = None,
        invoke: InvokeFn | None = None,
        collector: Any | None = None,
    ) -> None:
        self._model_factory = model_factory
        self._policy_loader = policy_loader or _default_policy_loader
        self._invoke = invoke
        # Optional LocalToolEvidenceCollector — when present, an ``action="audit"``
        # rule that fires emits a ``custom:CustomizeAudit`` record instead of
        # overriding (WS-B). ``None`` (e.g. the egress build path) → audit rules
        # are a safe no-op (never block, never raise).
        self._collector = collector

    async def on_after_tool(
        self,
        *,
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> dict[str, Any] | None:
        return await self._decide(
            tool=tool, result=result, tool_context=tool_context
        )

    async def apply_after_tool(
        self,
        ctx: Any,
        *,
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> dict[str, Any] | None:
        """Typed-context entry point (stateless — ``ctx`` unused)."""
        _ = (ctx, args)
        return await self._decide(
            tool=tool, result=result, tool_context=tool_context
        )

    async def _decide(
        self, *, tool: Any, result: Any, tool_context: Any = None
    ) -> dict[str, Any] | None:
        try:
            from magi_agent.config.flags import flag_profile_bool

            if not (
                flag_profile_bool("MAGI_CUSTOMIZE_VERIFICATION_ENABLED")
                and flag_profile_bool("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED")
            ):
                return None
            policy = self._policy_loader()
            rules = policy.enabled_llm_criterion_rules(fires_at="after_tool_use")
            if not rules:
                return None
            tool_name = _tool_name(tool)
            result_text = _result_text(result)
            turn_id = getattr(tool_context, "invocation_id", None) or "local-turn"
            session_id = (
                getattr(getattr(tool_context, "session", None), "id", None)
                or "cli-session"
            )
            for rule in rules:
                override = await self._eval_rule(
                    rule,
                    tool_name=tool_name,
                    result_text=result_text,
                    session_id=session_id,
                    turn_id=turn_id,
                )
                # ``override`` is non-None only for blocking (``override``)
                # rules; an ``audit`` rule that fires emits and returns None so
                # the loop continues (audit never short-circuits ingestion).
                if override is not None:
                    return override
            return None
        except Exception:
            return None

    async def _eval_rule(
        self,
        rule: dict[str, Any],
        *,
        tool_name: str,
        result_text: str,
        session_id: str,
        turn_id: str,
    ) -> dict[str, Any] | None:
        payload = rule.get("what", {}).get("payload", {})
        if not isinstance(payload, dict):
            return None
        tool_match = payload.get("toolMatch")
        if not isinstance(tool_match, list) or tool_name not in tool_match:
            return None

        content_match = payload.get("contentMatch")
        has_content = isinstance(content_match, dict)
        if has_content and not _content_matches(content_match, result_text):
            # Pre-filter did not match → rule does not fire (and LLM is skipped).
            return None

        criterion = payload.get("criterion")
        if isinstance(criterion, str) and criterion.strip():
            if self._model_factory is None:
                return None  # LLM sub-mode inert without a critic model.
            evidence_context = self._evidence_context_for(payload, turn_id)
            passed, reason = await evaluate_criterion(
                criterion=criterion,
                draft_text=result_text,
                model_factory=self._model_factory,
                invoke=self._invoke,
                evidence_context=evidence_context,
            )
            if passed:
                return None
            return self._fire(
                rule, reason, tool_name=tool_name, session_id=session_id, turn_id=turn_id
            )

        # Pure-deterministic mode: contentMatch alone fired.
        if has_content:
            return self._fire(
                rule,
                "content-match",
                tool_name=tool_name,
                session_id=session_id,
                turn_id=turn_id,
            )
        return None

    def _evidence_context_for(
        self, payload: dict[str, Any], turn_id: str
    ) -> Any | None:
        """Project this turn's evidence for an evidence-grounded criterion.

        Returns ``None`` (evidence-blind, byte-identical judge) unless
        ``MAGI_EVIDENCE_GROUNDED_CRITIC_ENABLED`` is on, the rule declares
        ``evidenceRefs``, and an evidence collector is wired. Best-effort: any
        fault degrades to ``None`` rather than dropping the after-tool gate.
        """
        try:
            from magi_agent.config.flags import flag_profile_bool  # noqa: PLC0415

            if self._collector is None:
                return None
            if not flag_profile_bool("MAGI_EVIDENCE_GROUNDED_CRITIC_ENABLED"):
                return None
            evidence_refs = payload.get("evidenceRefs")
            if not (isinstance(evidence_refs, list) and evidence_refs):
                return None
            collect = getattr(self._collector, "collect_for_turn", None)
            records = collect(turn_id) if callable(collect) else ()
            from magi_agent.customize.criterion_engine import (  # noqa: PLC0415
                project_evidence_for_criterion,
            )

            return project_evidence_for_criterion(records, evidence_refs)
        except Exception:
            return None

    def _fire(
        self,
        rule: dict[str, Any],
        reason: str,
        *,
        tool_name: str,
        session_id: str,
        turn_id: str,
    ) -> dict[str, Any] | None:
        """Dispatch a fired rule by action.

        ``audit`` → emit a typed ``custom:CustomizeAudit`` record (non-blocking,
        returns None). Any other action (``override``) → return the block
        override (byte-identical to the prior behavior).
        """
        if rule.get("action") == "audit":
            self._emit_audit(
                rule,
                reason,
                tool_name=tool_name,
                session_id=session_id,
                turn_id=turn_id,
            )
            return None
        return self._override(rule, reason)

    def _emit_audit(
        self,
        rule: dict[str, Any],
        reason: str,
        *,
        tool_name: str,
        session_id: str,
        turn_id: str,
    ) -> None:
        """Emit one ``custom:CustomizeAudit`` EvidenceRecord for a fired audit rule.

        Carries only rule metadata + a redacted reason — never raw tool output or
        conversation (least privilege). Fail-soft: a missing collector or any
        error is a silent no-op so an audit rule can never wedge tool ingestion.
        """
        collector = self._collector
        if collector is None:
            return
        try:
            import time

            from magi_agent.evidence.types import EvidenceRecord
            from magi_agent.runtime.receipt_redaction import sanitize_public_text

            rule_id = rule.get("id")
            record = EvidenceRecord.model_validate(
                {
                    "type": "custom:CustomizeAudit",
                    "status": "ok",
                    "observedAt": int(time.time() * 1000),
                    "source": {"kind": "tool_trace", "toolName": tool_name},
                    "fields": {
                        "evidenceRef": f"evidence:customize-audit:{rule_id}",
                        "ruleId": rule_id,
                        "kind": "llm_criterion",
                        "firesAt": "after_tool_use",
                        "action": "audit",
                        "matched": True,
                        "reason": sanitize_public_text(str(reason)),
                    },
                }
            )
            collector.record_audit_evidence_for_turn(
                session_id=session_id,
                turn_id=turn_id,
                tool_name=tool_name,
                record=record,
                tool_call_id=f"customize-audit:{rule_id}",
            )
        except Exception:
            return

    @staticmethod
    def _override(rule: dict[str, Any], reason: str) -> dict[str, Any]:
        rule_id = rule.get("id")
        return {
            "response_type": CUSTOMIZE_AFTER_TOOL_BLOCK_TYPE,
            "status": "blocked",
            "blocked_by": "customize_after_tool",
            "rule_id": rule_id,
            "reason": reason,
            "message": (
                f"Tool result blocked by customize rule {rule_id!r}: {reason}"
            ),
        }
