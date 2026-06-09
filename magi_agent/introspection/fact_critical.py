"""Fact-critical turn classifier for the egress critic gate (PR3).

Decides whether a user-visible chat turn warrants the evidence-grounded egress
critic. The decision is two-stage and NEVER uses regex/pattern-matching on the
user's message text (hard project policy):

1. **Deterministic signal (free):** consult the already-built
   :class:`~magi_agent.introspection.projection.SessionEvidenceView`. If the
   turn had NO evidence-bearing activity at all (no ``files_read`` and no
   ``tool_calls``) there is nothing to ground a fact-check against, so the turn
   is NOT fact-critical and the critic is skipped — zero model calls.

2. **Semantic decision (only when there WAS evidence-bearing activity):**
   whether the user's query is a verification / fact-sensitive question is
   decided by a Haiku-class LLM semantic classification, mirroring
   ``magi_agent.cli.readonly_classifier`` (ADK async-generator call + strict
   JSON parse + ``asyncio.wait_for`` timeout + fail-safe + per-key cache). This
   is a semantic judgement, NOT a regex over the message.

Fail-safe default: any error / timeout / missing model defaults to NOT
fact-critical (fail-OPEN — never block a normal answer), and an evidence note is
emitted via the optional sink.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from magi_agent.introspection.projection import SessionEvidenceView

__all__ = [
    "FACT_CRITICAL_EVIDENCE_TYPE",
    "FactCriticalClassifier",
    "FactCriticalDecision",
]

# Registered custom evidence type — satisfies validate_evidence_type_name().
FACT_CRITICAL_EVIDENCE_TYPE: str = "custom:FactCriticalClassification"

# Model env override — allows a faster/cheaper (Haiku-class) model.
_ENV_MODEL_OVERRIDE = "MAGI_FACT_CRITICAL_MODEL"

# Timeout env override for the LLM call (seconds). Default: 8.
_ENV_TIMEOUT_OVERRIDE = "MAGI_FACT_CRITICAL_TIMEOUT"
_DEFAULT_LLM_TIMEOUT_SECS: float = 8.0

# Cap the user query length fed to the classifier (untrusted, possibly large).
_MAX_QUERY_CHARS: int = 4000
# Cap the reason string stored in evidence.
_MAX_REASON_CHARS: int = 500

_FACT_CRITICAL_SYSTEM_INSTRUCTION = (
    "You are a turn classifier for an AI agent. Reply with ONLY a JSON object: "
    '{"fact_critical": <bool>, "reason": "<one-sentence reason>"}'
)

_FACT_CRITICAL_PROMPT_TEMPLATE = """\
The agent just did evidence-bearing work (read files and/or called tools) while
answering the user. Decide whether the user's QUERY is FACT-SENSITIVE — i.e. a
verification / factual / "what did you find / is this true / did you do X"
question whose answer must be grounded in that real evidence, as opposed to
chit-chat, opinion, brainstorming, or a purely creative/open-ended request.

Rules:
- Asks to verify, confirm, check, report findings, or state facts grounded in
  the work just done -> fact_critical = true.
- Greeting, small talk, opinion, creative writing, open-ended ideation with no
  factual claim to verify -> fact_critical = false.
- If unsure -> false (do not over-trigger the critic).

User query:
{query}

Reply with ONLY a JSON object with no additional text:
{{"fact_critical": <bool>, "reason": "<one-sentence reason>"}}
"""


class FactCriticalDecision(BaseModel):
    """Result of the fact-critical classification."""

    model_config = ConfigDict(frozen=True)

    fact_critical: bool
    reason: str
    # "no_evidence" | "cache" | "llm" | "classifier_error"
    source: str
    model: str | None = None


class FactCriticalClassifier:
    """Two-stage (deterministic signal -> semantic LLM) fact-critical classifier.

    Parameters
    ----------
    model_factory:
        Zero-argument callable returning a LiteLlm-compatible model object
        (must expose ``generate_content_async``). When ``None`` the semantic
        step fails OPEN (NOT fact-critical). Tests MUST inject a fake here so no
        real network call happens.
    evidence_sink:
        Optional ``Callable[[dict], None]`` receiving every decision record.
        Never raises (errors suppressed).
    """

    def __init__(
        self,
        *,
        model_factory: Callable[[], object] | None = None,
        evidence_sink: Callable[[dict], None] | None = None,
    ) -> None:
        self._model_factory = model_factory
        self._evidence_sink = evidence_sink
        # Per-instance (per-turn/session) cache keyed by the query digest.
        self._cache: dict[str, FactCriticalDecision] = {}

    async def classify(
        self,
        *,
        user_query: str,
        view: "SessionEvidenceView",
    ) -> FactCriticalDecision:
        """Return the fact-critical decision for this turn.

        Stage 1 (deterministic, free): no evidence-bearing activity -> not
        fact-critical, 0 model calls. Stage 2 (semantic): a Haiku-class LLM
        decides whether the query is fact-sensitive. Fail-open on any error.
        """
        if not _has_evidence_activity(view):
            decision = FactCriticalDecision(
                fact_critical=False,
                reason="no evidence-bearing activity this turn; nothing to ground",
                source="no_evidence",
                model=None,
            )
            self._emit(decision)
            return decision

        key = _query_cache_key(user_query)
        cached = self._cache.get(key)
        if cached is not None:
            decision = cached.model_copy(update={"source": "cache"})
            self._emit(decision)
            return decision

        decision = await self._llm_classify(user_query)
        # Only cache genuine LLM verdicts (not transient errors), so a later
        # turn can retry classification after a transient failure.
        if decision.source == "llm":
            self._cache[key] = decision
        self._emit(decision)
        return decision

    async def _llm_classify(self, user_query: str) -> FactCriticalDecision:
        model_name: str | None = None
        try:
            model = self._resolve_model()
            if model is None:
                raise RuntimeError("no model available for fact-critical classification")
            model_name = getattr(model, "model", None) or getattr(model, "_model", None)

            prompt = _FACT_CRITICAL_PROMPT_TEMPLATE.format(
                query=user_query[:_MAX_QUERY_CHARS] or "(empty)",
            )
            raw_text = await asyncio.wait_for(
                _invoke_llm(model, prompt),
                timeout=_resolve_timeout(),
            )
            parsed = _parse_llm_response(raw_text)
            if parsed is None:
                raise ValueError(f"LLM returned non-parseable response: {raw_text!r}")
            verdict = bool(parsed["fact_critical"])
            reason = str(parsed.get("reason", ""))[:_MAX_REASON_CHARS]
            return FactCriticalDecision(
                fact_critical=verdict,
                reason=reason or "llm classification",
                source="llm",
                model=model_name,
            )
        except asyncio.TimeoutError:
            return FactCriticalDecision(
                fact_critical=False,
                reason="classifier timeout",
                source="classifier_error",
                model=model_name,
            )
        except Exception as exc:  # noqa: BLE001 — fail OPEN (not fact-critical)
            return FactCriticalDecision(
                fact_critical=False,
                reason=f"{type(exc).__name__}: {exc}"[:_MAX_REASON_CHARS],
                source="classifier_error",
                model=model_name,
            )

    def _resolve_model(self) -> object | None:
        if self._model_factory is None:
            return None
        try:
            return self._model_factory()
        except Exception:  # noqa: BLE001
            return None

    def _emit(self, decision: FactCriticalDecision) -> None:
        if self._evidence_sink is None:
            return
        try:
            self._evidence_sink(
                {
                    "type": FACT_CRITICAL_EVIDENCE_TYPE,
                    "fact_critical": decision.fact_critical,
                    "reason": decision.reason,
                    "source": decision.source,
                    "model": decision.model,
                }
            )
        except Exception:  # noqa: BLE001 — evidence sink errors never break the gate
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_evidence_activity(view: "SessionEvidenceView") -> bool:
    """Deterministic, free signal: any files read or tools called this turn."""
    return bool(view.files_read) or bool(view.tool_calls)


def _query_cache_key(user_query: str) -> str:
    return hashlib.sha256(user_query.encode("utf-8", "replace")).hexdigest()


def _resolve_timeout() -> float:
    raw = os.environ.get(_ENV_TIMEOUT_OVERRIDE, "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return _DEFAULT_LLM_TIMEOUT_SECS


async def _invoke_llm(model: object, prompt: str) -> str:
    """Invoke the model using the ADK async-generator contract.

    Mirrors ``ReadOnlyClassifier._invoke_llm``: builds an ``LlmRequest`` with the
    prompt as a user content part, consumes the async generator returned by
    ``model.generate_content_async(llm_request, stream=False)``, and concatenates
    all ``LlmResponse.content.parts[i].text``.
    """
    from google.adk.models.llm_request import LlmRequest  # noqa: PLC0415
    from google.genai import types  # noqa: PLC0415

    llm_request = LlmRequest(
        config=types.GenerateContentConfig(
            system_instruction=_FACT_CRITICAL_SYSTEM_INSTRUCTION,
        ),
        contents=[
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt)],
            )
        ],
    )
    collected: list[str] = []
    async for resp in model.generate_content_async(llm_request, stream=False):  # type: ignore[union-attr]
        if resp.content and resp.content.parts:
            for part in resp.content.parts:
                if part.text:
                    collected.append(part.text)
    return "".join(collected)


def _parse_llm_response(text: str) -> dict | None:
    """Parse the strict-JSON ``{"fact_critical": bool, ...}`` response."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = lines[1:-1] if len(lines) >= 3 else lines
        text = "\n".join(inner).strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    if "fact_critical" not in parsed:
        return None
    if not isinstance(parsed["fact_critical"], bool):
        return None
    return parsed
