"""Evidence-grounded egress critic gate (PR3).

Before a user-visible chat answer is sent, for FACT-CRITICAL turns, this gate
verifies the draft answer against the agent's REAL recorded evidence (the PR1
:class:`~magi_agent.introspection.projection.SessionEvidenceView`: files
actually read, tools actually called, verifier verdicts) WITHOUT reading raw
history. It catches hallucination / misstatement before egress.

Design (post-pivot — see the design doc header for background):
  - The deterministic "claim<->view" and "phase invariant" layers from the
    original design are INERT in the general chat path today (no phase-evidence
    producer; agent "claims" are only emitted by the research harness). They are
    deferred. PR3 ships the part that works today: a lean LLM critic grounded in
    the real projection.
  - Run the fact-critical classifier first. Not fact-critical -> NO critic call
    (zero added cost), status ``None`` (passed).
  - Fact-critical -> one lean Haiku critic call comparing the COMPACT view to
    the draft + query. grounded&relevant -> "passed"; not grounded -> a SOFT
    "missing_evidence" signal (NOT a hard block); critic error/timeout ->
    fail-open (status ``None``).

**Fail-open always**: this gate NEVER blocks or rewrites the answer in v1. It
only computes a status + emits an evidence record. Regeneration/annotation is a
documented follow-up.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from magi_agent.introspection.fact_critical import FactCriticalClassifier
from magi_agent.introspection.projection import SessionEvidenceView

__all__ = [
    "EGRESS_CRITIC_EVIDENCE_TYPE",
    "EgressCheckResult",
    "run_egress_critic_check",
]

# verifier_evidence_status enum used by the chat.py response payload.
EgressVerifierStatus = Literal["passed", "missing_evidence", "failed"]

# Registered custom evidence type — satisfies validate_evidence_type_name().
EGRESS_CRITIC_EVIDENCE_TYPE: str = "custom:EgressCriticCheck"

_ENV_TIMEOUT_OVERRIDE = "MAGI_EGRESS_CRITIC_TIMEOUT"
_DEFAULT_LLM_TIMEOUT_SECS: float = 10.0

_MAX_DRAFT_CHARS: int = 6000
_MAX_QUERY_CHARS: int = 4000
_MAX_REASON_CHARS: int = 500
# Cap how much of each view slice is rendered into the prompt (lean view).
_MAX_VIEW_ITEMS: int = 40

_CRITIC_SYSTEM_INSTRUCTION = (
    "You are an answer-grounding critic for an AI agent. Reply with ONLY a JSON "
    'object: {"grounded": <bool>, "relevant": <bool>, "reason": "<one sentence>"}'
)

_CRITIC_PROMPT_TEMPLATE = """\
You verify an agent's DRAFT answer against the REAL evidence it actually
recorded this turn (files it actually read, tools it actually called, and
verifier verdicts). You are NOT given the raw transcript — only this compact,
trustworthy projection.

Decide two things:
1. relevant: does the draft answer actually address the user's query?
2. grounded: does the draft AVOID claims that contradict, or are unsupported by,
   the real evidence view? (General knowledge the agent legitimately knows is
   fine; the concern is fabricated claims about what it found/did this turn.)

If unsure, prefer grounded=true / relevant=true (do not over-flag).

User query:
{query}

Agent's REAL evidence view (JSON):
{view}

Agent's DRAFT answer:
{draft}

Reply with ONLY a JSON object with no additional text:
{{"grounded": <bool>, "relevant": <bool>, "reason": "<one sentence>"}}
"""


class EgressCheckResult(BaseModel):
    """Outcome of the egress critic check.

    ``status`` maps directly onto chat.py's ``verifier_evidence_status``:
      - ``None``              -> no signal (not fact-critical OR fail-open error).
      - ``"passed"``          -> grounded & relevant.
      - ``"missing_evidence"``-> not grounded (soft signal, NOT a hard block).
      - ``"failed"``          -> reserved; not emitted by v1 (kept for contract).
    """

    model_config = ConfigDict(frozen=True)

    status: EgressVerifierStatus | None = None
    fact_critical: bool = False
    critic_invoked: bool = False
    grounded: bool | None = None
    relevant: bool | None = None
    reason: str = ""
    # "not_fact_critical" | "grounded" | "ungrounded" | "fact_critical_error"
    #  | "critic_error"
    source: str = "not_fact_critical"
    model: str | None = None


async def run_egress_critic_check(
    *,
    draft_text: str,
    user_query: str,
    view: SessionEvidenceView,
    model_factory: Callable[[], object] | None,
    fact_critical_model_factory: Callable[[], object] | None = None,
    evidence_sink: Callable[[dict], None] | None = None,
) -> EgressCheckResult:
    """Run the (fact-critical -> lean critic) egress check. Fail-open always.

    Parameters
    ----------
    draft_text:
        The internal draft answer text about to be sent to the user.
    user_query:
        The user's latest message text for this turn.
    view:
        PR1 ``SessionEvidenceView`` projected from the live session evidence.
    model_factory:
        Factory for the critic model (Haiku-class). When ``None`` the critic
        step fails open (status ``None``). Tests inject a fake.
    fact_critical_model_factory:
        Optional separate factory for the fact-critical classifier. Defaults to
        ``model_factory`` when omitted.
    evidence_sink:
        Optional sink receiving fact-critical AND critic evidence records.
    """
    classifier = FactCriticalClassifier(
        model_factory=fact_critical_model_factory or model_factory,
        evidence_sink=evidence_sink,
    )
    decision = await classifier.classify(user_query=user_query, view=view)

    if not decision.fact_critical:
        # Distinguish a clean "not fact-critical" from a fail-open classifier
        # error so the receipt is honest, but both yield status=None (no block).
        source = (
            "fact_critical_error"
            if decision.source == "classifier_error"
            else "not_fact_critical"
        )
        result = EgressCheckResult(
            status=None,
            fact_critical=False,
            critic_invoked=False,
            reason=decision.reason,
            source=source,
            model=decision.model,
        )
        _emit(evidence_sink, result)
        return result

    critic = await _run_critic(
        draft_text=draft_text,
        user_query=user_query,
        view=view,
        model_factory=model_factory,
    )
    _emit(evidence_sink, critic)
    return critic


async def _run_critic(
    *,
    draft_text: str,
    user_query: str,
    view: SessionEvidenceView,
    model_factory: Callable[[], object] | None,
) -> EgressCheckResult:
    model_name: str | None = None
    try:
        model = model_factory() if model_factory is not None else None
        if model is None:
            raise RuntimeError("no model available for egress critic")
        model_name = getattr(model, "model", None) or getattr(model, "_model", None)

        prompt = _CRITIC_PROMPT_TEMPLATE.format(
            query=user_query[:_MAX_QUERY_CHARS] or "(empty)",
            view=_render_view(view),
            draft=draft_text[:_MAX_DRAFT_CHARS] or "(empty)",
        )
        raw_text = await asyncio.wait_for(
            _invoke_llm(model, prompt),
            timeout=_resolve_timeout(),
        )
        parsed = _parse_critic_response(raw_text)
        if parsed is None:
            raise ValueError(f"critic returned non-parseable response: {raw_text!r}")
        grounded = bool(parsed["grounded"])
        relevant = bool(parsed["relevant"])
        reason = str(parsed.get("reason", ""))[:_MAX_REASON_CHARS]
        if grounded and relevant:
            status: EgressVerifierStatus | None = "passed"
            source = "grounded"
        else:
            # Not grounded / not relevant -> SOFT missing_evidence signal.
            status = "missing_evidence"
            source = "ungrounded"
        return EgressCheckResult(
            status=status,
            fact_critical=True,
            critic_invoked=True,
            grounded=grounded,
            relevant=relevant,
            reason=reason or "critic verdict",
            source=source,
            model=model_name,
        )
    except asyncio.TimeoutError:
        return EgressCheckResult(
            status=None,
            fact_critical=True,
            critic_invoked=True,
            reason="critic timeout",
            source="critic_error",
            model=model_name,
        )
    except Exception as exc:  # noqa: BLE001 — fail open (no block)
        return EgressCheckResult(
            status=None,
            fact_critical=True,
            critic_invoked=True,
            reason=f"{type(exc).__name__}: {exc}"[:_MAX_REASON_CHARS],
            source="critic_error",
            model=model_name,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render_view(view: SessionEvidenceView) -> str:
    """Render the COMPACT view as lean JSON (capped), never raw transcript."""
    payload = {
        "session_id": view.scope.session_id,
        "turns_covered": list(view.scope.turns_covered)[:_MAX_VIEW_ITEMS],
        "files_read": [
            {"path": f.path, "sha256": f.sha256, "bytes": f.bytes}
            for f in view.files_read[:_MAX_VIEW_ITEMS]
        ],
        "tool_calls": [
            {"name": t.name, "status": t.status}
            for t in view.tool_calls[:_MAX_VIEW_ITEMS]
        ],
        "verdicts": [
            {"stage": v.stage, "result": v.result}
            for v in view.verdicts[:_MAX_VIEW_ITEMS]
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _resolve_timeout() -> float:
    raw = os.environ.get(_ENV_TIMEOUT_OVERRIDE, "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return _DEFAULT_LLM_TIMEOUT_SECS


async def _invoke_llm(model: object, prompt: str) -> str:
    """Invoke the model using the ADK async-generator contract (mirrors PR2)."""
    from google.adk.models.llm_request import LlmRequest  # noqa: PLC0415
    from google.genai import types  # noqa: PLC0415

    llm_request = LlmRequest(
        config=types.GenerateContentConfig(
            system_instruction=_CRITIC_SYSTEM_INSTRUCTION,
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


def _parse_critic_response(text: str) -> dict | None:
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
    if "grounded" not in parsed or "relevant" not in parsed:
        return None
    if not isinstance(parsed["grounded"], bool) or not isinstance(parsed["relevant"], bool):
        return None
    return parsed


def _emit(
    evidence_sink: Callable[[dict], None] | None,
    result: EgressCheckResult,
) -> None:
    if evidence_sink is None:
        return
    try:
        evidence_sink(
            {
                "type": EGRESS_CRITIC_EVIDENCE_TYPE,
                "status": result.status,
                "fact_critical": result.fact_critical,
                "critic_invoked": result.critic_invoked,
                "grounded": result.grounded,
                "relevant": result.relevant,
                "reason": result.reason,
                "source": result.source,
                "model": result.model,
            }
        )
    except Exception:  # noqa: BLE001 — evidence sink errors never break the gate
        pass
