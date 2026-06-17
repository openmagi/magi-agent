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
    ) -> None:
        self._model_factory = model_factory
        self._policy_loader = policy_loader or _default_policy_loader
        self._invoke = invoke

    async def on_after_tool(
        self,
        *,
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> dict[str, Any] | None:
        return await self._decide(tool=tool, result=result)

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
        _ = (ctx, args, tool_context)
        return await self._decide(tool=tool, result=result)

    async def _decide(self, *, tool: Any, result: Any) -> dict[str, Any] | None:
        try:
            from magi_agent.config.flags import flag_bool

            if not (
                flag_bool("MAGI_CUSTOMIZE_VERIFICATION_ENABLED")
                and flag_bool("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED")
            ):
                return None
            policy = self._policy_loader()
            rules = policy.enabled_llm_criterion_rules(fires_at="after_tool_use")
            if not rules:
                return None
            tool_name = _tool_name(tool)
            result_text = _result_text(result)
            for rule in rules:
                override = await self._eval_rule(
                    rule, tool_name=tool_name, result_text=result_text
                )
                if override is not None:
                    return override
            return None
        except Exception:
            return None

    async def _eval_rule(
        self, rule: dict[str, Any], *, tool_name: str, result_text: str
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
            passed, reason = await evaluate_criterion(
                criterion=criterion,
                draft_text=result_text,
                model_factory=self._model_factory,
                invoke=self._invoke,
            )
            if passed:
                return None
            return self._override(rule, reason)

        # Pure-deterministic mode: contentMatch alone fired.
        if has_content:
            return self._override(rule, "content-match")
        return None

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
