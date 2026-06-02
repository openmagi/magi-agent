"""LLM hook executor — classifies hook context via a lightweight LLM call.

Protocol
--------
- Renders ``manifest.prompt_template`` with sanitized hook context variables.
- Calls the classifier model (Haiku / flash) via ``google.genai``.
- Parses the first line of the response for ALLOW, DENY, or ASK keywords.
- Returns a ``HookResult`` with ``action="permission_decision"`` and the parsed
  decision, or ``action="permission_decision"`` with ``decision="ask"`` if the
  classifier output is ambiguous.

Token budget
------------
The rendered prompt is truncated to ``manifest.max_prompt_tokens * 4`` chars
(conservative bytes-to-tokens estimate) before being sent to the classifier.

Fail policy
-----------
- On timeout or exception: ``continue`` (fail-open) or ``block`` (fail-closed)
  based on ``manifest.fail_open``.

Security
--------
- Context values are sanitized via the shared ``_build_sanitized_hook_input``
  helper before template rendering.
- Interpolated values are wrapped in ``<context_value>`` XML tags to prevent
  prompt injection from user-controlled data.
- The ``prompt_template`` is operator-supplied — operators are trusted.
- Classifier responses are parsed from the first line only to resist
  adversarial multi-line output manipulation.
- Gemini safety_settings are applied to the classifier call itself.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any

from openmagi_core_agent.hooks.context import HookContext
from openmagi_core_agent.hooks.executors import _REGISTRY
from openmagi_core_agent.hooks.executors.sanitize import _build_sanitized_hook_input
from openmagi_core_agent.hooks.manifest import HookManifest
from openmagi_core_agent.hooks.result import HookResult

logger = logging.getLogger(__name__)

__all__ = ["LLMHookExecutor"]

_DEFAULT_CLASSIFIER_MODEL = "gemini-2.0-flash"

_DECISION_RE = re.compile(r"\b(ALLOW|DENY|ASK)\b", re.IGNORECASE)

# Singleton client — created on first use, reused across calls.
_genai_client: object | None = None
_genai_client_key: str | None = None


def _resolve_classifier_model(context: HookContext) -> str:
    env_model = os.environ.get("MAGI_LLM_HOOK_CLASSIFIER_MODEL", "").strip()
    if env_model:
        return env_model
    if context.classifier_model:
        return context.classifier_model
    return _DEFAULT_CLASSIFIER_MODEL


def _get_genai_client() -> Any:
    """Return a cached google.genai.Client singleton.

    Recreated only if the API key environment variable changes.
    """
    global _genai_client, _genai_client_key
    from google import genai

    api_key = os.environ.get("GOOGLE_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
    if _genai_client is None or api_key != _genai_client_key:
        _genai_client = genai.Client(api_key=api_key) if api_key else genai.Client()
        _genai_client_key = api_key
        if not api_key:
            logger.warning(
                "LLM hook executor: no GOOGLE_API_KEY or GEMINI_API_KEY set; "
                "classifier calls may fail or use ADC"
            )
    return _genai_client


def _xml_wrap(value: str) -> str:
    """Wrap a value in XML tags to isolate it from the prompt template."""
    sanitized = value.replace("</context_value>", "")
    return f"<context_value>{sanitized}</context_value>"


def _render_prompt(template: str, context_vars: dict[str, Any]) -> str:
    """Render prompt template with context variables using safe str.format_map.

    Unknown placeholders are left as-is rather than raising KeyError.
    String values are wrapped in XML tags to prevent prompt injection.
    """

    class _SafeDict(dict[str, Any]):
        def __missing__(self, key: str) -> str:
            return f"{{{key}}}"

    flat: dict[str, Any] = {}
    for k, v in context_vars.items():
        if isinstance(v, str):
            flat[k] = _xml_wrap(v)
        elif isinstance(v, (int, float, bool)):
            flat[k] = v
        elif v is None:
            flat[k] = ""
        else:
            flat[k] = _xml_wrap(str(v)[:500])

    return template.format_map(_SafeDict(flat))


def _truncate_prompt(prompt: str, max_tokens: int) -> str:
    max_chars = max_tokens * 4
    if len(prompt) <= max_chars:
        return prompt
    return prompt[:max_chars] + "\n[... truncated]"


def _parse_llm_decision(text: str) -> tuple[str, str | None]:
    """Parse classifier response for ALLOW/DENY/ASK.

    Only the first line is examined to prevent adversarial multi-line output
    from steering the decision. Ambiguous output defaults to "ask".
    """
    first_line = text.strip().split("\n")[0] if text.strip() else ""
    match = _DECISION_RE.search(first_line)
    if not match:
        return "ask", None

    raw = match.group(1).upper()
    decision_map = {"ALLOW": "approve", "DENY": "deny", "ASK": "ask"}
    decision = decision_map[raw]

    after = first_line[match.end():].strip()
    reason = after[:500] if after else None

    return decision, reason


async def _call_classifier(model: str, prompt: str, timeout_s: float) -> str:
    """Call the classifier model via google.genai.

    Uses asyncio.to_thread to avoid blocking the event loop since
    the google.genai client is synchronous.
    """
    from google import genai

    client = _get_genai_client()

    def _sync_call() -> str:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                max_output_tokens=256,
                temperature=0.0,
                safety_settings=[
                    genai.types.SafetySetting(
                        category="HARM_CATEGORY_DANGEROUS_CONTENT",
                        threshold="BLOCK_MEDIUM_AND_ABOVE",
                    ),
                    genai.types.SafetySetting(
                        category="HARM_CATEGORY_HARASSMENT",
                        threshold="BLOCK_MEDIUM_AND_ABOVE",
                    ),
                ],
            ),
        )
        return response.text or ""

    return await asyncio.wait_for(
        asyncio.to_thread(_sync_call),
        timeout=timeout_s,
    )


class LLMHookExecutor:
    """Executes hooks by calling a classifier LLM (Haiku/flash) with a prompt template.

    Implements the ``HookExecutor`` protocol.
    """

    async def execute(self, context: HookContext, manifest: HookManifest) -> HookResult:
        assert manifest.prompt_template is not None, "LLMHookExecutor requires manifest.prompt_template"

        start_ms = time.monotonic() * 1000
        model = _resolve_classifier_model(context)
        timeout_s = manifest.timeout_ms / 1000.0

        sanitized = _build_sanitized_hook_input(context, manifest)
        prompt = _render_prompt(manifest.prompt_template, sanitized)
        prompt = _truncate_prompt(prompt, manifest.max_prompt_tokens)

        try:
            raw_response = await _call_classifier(model, prompt, timeout_s)
        except asyncio.TimeoutError:
            logger.warning(
                "llm hook '%s' timed out after %.1fs (model=%s)",
                manifest.name,
                timeout_s,
                model,
            )
            if manifest.fail_open:
                return HookResult(action="continue")
            return HookResult(
                action="block",
                reason=f"Hook '{manifest.name}' timed out after {manifest.timeout_ms}ms",
            )
        except Exception:
            logger.exception(
                "llm hook '%s' raised an unexpected exception (model=%s)",
                manifest.name,
                model,
            )
            if manifest.fail_open:
                return HookResult(action="continue")
            return HookResult(
                action="block",
                reason=f"Hook '{manifest.name}' encountered an unexpected error",
            )

        latency_ms = time.monotonic() * 1000 - start_ms

        if latency_ms > manifest.timeout_ms * 0.8:
            logger.warning(
                "llm hook '%s' near timeout: %.0fms / %dms",
                manifest.name,
                latency_ms,
                manifest.timeout_ms,
            )

        decision, reason = _parse_llm_decision(raw_response)

        metadata: dict[str, object] = {
            "llm_hook_classification": {
                "model": model,
                "decision": decision,
                "latency_ms": round(latency_ms, 1),
                "raw_response": raw_response[:1000],
                "prompt_chars": len(prompt),
            },
        }

        return HookResult(
            action="permission_decision",
            decision=decision,  # type: ignore[arg-type]
            reason=reason,
            metadata=metadata,
        )


_REGISTRY["llm"] = LLMHookExecutor()
