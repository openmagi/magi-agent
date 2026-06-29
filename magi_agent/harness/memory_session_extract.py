"""Session-end auto-extraction of declarative facts (PR4 — Hermes timing).

Today the model only persists long-term memory when it explicitly calls the
``MemoryWrite`` tool (and the gated N-turn background ``memory_review`` harness).
Hermes ALSO extracts memory at the SESSION BOUNDARY: when a session ends
(``on_session_end``) it re-reads the whole transcript ONCE and saves the
durable facts the model surfaced during the conversation.

This module ports that ``on_session_end`` timing. Extracting once per session
(rather than forking a review on every turn) bounds both cost and risk: a single
cheap-model call per boundary, never on the hot turn loop.

Pipeline (all reused, nothing reimplemented):
  1. ``extract_session_facts(messages)`` — a CHEAP model proposes declarative
     fact candidates from the transcript (ADK async-generator contract, mirrors
     ``cli/readonly_classifier``). Fail-soft: any model error → ``[]``.
  2. ``magi_agent.memory.declarative_filter.is_declarative_result`` drops
     task-state ("PR #123 merged", commit SHAs, "currently doing X", …).
  3. ``LocalFileMemoryProvider.remember`` performs the actual write — it already
     enforces the write gate (``write_enabled`` / ``MAGI_MEMORY_WRITE_ENABLED``),
     redaction, path-safety, byte caps, and the MEMORY.md/USER.md allowlist. The
     harness PINS ``target_file="MEMORY.md"`` so the agent can never reach
     SOUL.md.

Safety posture (mirrors ``harness/memory_review.py``)
-----------------------------------------------------
* DEFAULT OFF. ``MAGI_MEMORY_SESSION_EXTRACT_ENABLED`` gates the whole feature.
  When it is off, ``on_session_end`` short-circuits with NO model call and NO
  write. Writes ADDITIONALLY require ``MAGI_MEMORY_WRITE_ENABLED`` (owned by the
  provider's own gate — this harness adds no write authority of its own).
* FAIL-SOFT. Neither the extractor model nor a provider write may raise into the
  caller's turn / session-close path. Every failure is swallowed and reflected
  in the receipt's counts / ``reason_codes``.
* The harness reimplements no redaction, no path-safety, no byte caps.

Forbidden imports: urllib, socket, subprocess, http, requests — none here.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.memory.declarative_filter import is_declarative_result


# ---------------------------------------------------------------------------
# Env gates
# ---------------------------------------------------------------------------

#: Feature gate (default OFF). ``on_session_end`` requires this to be truthy
#: before it ever calls the extractor or attempts a write.
MAGI_MEMORY_SESSION_EXTRACT_ENABLED_ENV: str = "MAGI_MEMORY_SESSION_EXTRACT_ENABLED"

#: Optional cheap-model override for the extractor (mirrors the SmartApprove
#: ``MAGI_SMART_APPROVE_MODEL`` override). When unset the resolved provider model
#: is used.
MAGI_MEMORY_SESSION_EXTRACT_MODEL_ENV: str = "MAGI_MEMORY_SESSION_EXTRACT_MODEL"

#: Timeout (seconds) for the extractor model call.
_MAGI_SESSION_EXTRACT_TIMEOUT_ENV: str = "MAGI_MEMORY_SESSION_EXTRACT_TIMEOUT"
_DEFAULT_EXTRACT_TIMEOUT_SECS: float = 15.0


#: The kind/label every session-extracted fact is written under.
_SESSION_EXTRACT_KIND = "session_extract"
#: Writes are PINNED here — the agent must never reach SOUL.md.
_SESSION_EXTRACT_TARGET = "MEMORY.md"

#: Extractor seam: transcript → candidate declarative fact strings. A live
#: cheap-model extractor (``extract_session_facts``) plugs in here; tests inject
#: a fake.
Extractor = Callable[[list[dict]], Sequence[str]]

SessionExtractStatus = Literal["disabled", "extracted"]


def _feature_enabled() -> bool:
    """True only when ``MAGI_MEMORY_SESSION_EXTRACT_ENABLED`` is explicitly truthy."""
    # I-1: route through the typed flag registry.
    from magi_agent.config.flags import flag_bool  # noqa: PLC0415

    return flag_bool(MAGI_MEMORY_SESSION_EXTRACT_ENABLED_ENV)


def _extract_timeout() -> float:
    raw = os.environ.get(_MAGI_SESSION_EXTRACT_TIMEOUT_ENV, "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return _DEFAULT_EXTRACT_TIMEOUT_SECS


# ---------------------------------------------------------------------------
# Receipt
# ---------------------------------------------------------------------------


class SessionExtractReceipt(BaseModel):
    """Summary of one session-end extraction pass.

    ``status`` is ``disabled`` when the feature gate is OFF — in that case the
    extractor was NOT called and nothing was written. Otherwise ``extracted``.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    status: SessionExtractStatus
    candidates: int = 0
    dropped_declarative: int = Field(default=0, alias="droppedDeclarative")
    attempted_writes: int = Field(default=0, alias="attemptedWrites")
    written: int = 0
    blocked: int = 0
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")


# ---------------------------------------------------------------------------
# Session-end seam
# ---------------------------------------------------------------------------


async def on_session_end(
    messages: list[dict],
    *,
    provider: Any,
    extractor: Extractor | None = None,
    model: Any | None = None,
) -> SessionExtractReceipt:
    """Extract durable facts from *messages* at a session boundary and persist them.

    Called ONCE at a session boundary (the ``/reset`` handler or a transport
    session close) with the conversation transcript. Fail-soft throughout: no
    model error and no provider write error may escape into the caller.

    Parameters
    ----------
    messages:
        The conversation transcript (``[{"role": ..., "content": ...}, ...]``).
    provider:
        A ``LocalFileMemoryProvider`` (or compatible) whose async ``remember``
        owns the write gate, redaction, path-safety, and byte caps.
    extractor:
        Optional injected extractor (test seam). When omitted, the real cheap
        model extractor (:func:`extract_session_facts`) is used with *model*.
    model:
        Optional model object passed to the default extractor. Ignored when
        *extractor* is supplied.

    Returns
    -------
    SessionExtractReceipt
        ``status='disabled'`` (feature gate off, no model call / no write) or
        ``status='extracted'`` with per-fact counts.
    """
    # Hard gate: feature OFF → no model call, no write, no-op.
    if not _feature_enabled():
        return SessionExtractReceipt(
            status="disabled", reasonCodes=("session_extract_disabled",)
        )

    transcript = list(messages or [])

    # 1. Extract candidates — fail-soft. A raising extractor yields zero
    #    candidates rather than crashing the session-close path.
    reason_codes: list[str] = []
    try:
        if extractor is not None:
            candidates = list(extractor(transcript))
        else:
            candidates = list(await extract_session_facts(transcript, model=model))
    except Exception:  # noqa: BLE001 — fail-soft, never escape the session close
        return SessionExtractReceipt(
            status="extracted",
            candidates=0,
            reasonCodes=("extractor_exception",),
        )

    # 2. Filter + 3. write each accepted candidate through the gated provider.
    dropped = 0
    written = 0
    blocked = 0
    attempted = 0

    for raw in candidates:
        fact = "" if raw is None else str(raw)
        if not is_declarative_result(fact).accepted:
            dropped += 1
            continue
        attempted += 1
        ok = await _write_fact(provider, fact)
        if ok:
            written += 1
        else:
            blocked += 1

    reason_codes.append("session_extract_completed")
    return SessionExtractReceipt(
        status="extracted",
        candidates=len(candidates),
        droppedDeclarative=dropped,
        attemptedWrites=attempted,
        written=written,
        blocked=blocked,
        reasonCodes=tuple(reason_codes),
    )


async def _write_fact(provider: Any, fact: str) -> bool:
    """Write one accepted fact through the provider's gated ``remember``.

    Returns True on a successful write, False otherwise. Any provider exception
    (write gate closed → ``UnsupportedMemoryOperationError``, byte-cap →
    ``ValueError``, or anything else) is swallowed and reported as a non-write
    (fail-soft). ``target_file`` is PINNED to MEMORY.md so SOUL.md is unreachable.
    """
    try:
        await provider.remember(
            {
                "body": fact,
                "kind": _SESSION_EXTRACT_KIND,
                "target_file": _SESSION_EXTRACT_TARGET,
            }
        )
        return True
    except Exception:  # noqa: BLE001 — gate-closed / cap / IO must never escape
        return False


# ---------------------------------------------------------------------------
# Real cheap-model extractor
# ---------------------------------------------------------------------------

_EXTRACT_SYSTEM_INSTRUCTION = (
    "You extract DURABLE, DECLARATIVE facts about the user from a conversation "
    "transcript: stable preferences, traits, profile facts, and standing "
    "decisions. Do NOT extract transient task-state (PR/issue numbers, commit "
    "SHAs, 'merged', 'deployed', 'currently doing X', timestamps). If nothing "
    "durable was surfaced, return an empty list. Reply with ONLY a JSON object: "
    '{"facts": ["<short declarative fact>", ...]}'
)

#: Cap the transcript size fed to the model (defensive — bound prompt cost).
_MAX_TRANSCRIPT_CHARS = 16_000


def _render_transcript(messages: list[dict]) -> str:
    """Render the transcript into a compact role-tagged block (bounded)."""
    lines: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "")).strip() or "unknown"
        content = msg.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        lines.append(f"{role}: {content}")
    rendered = "\n".join(lines)
    if len(rendered) > _MAX_TRANSCRIPT_CHARS:
        rendered = rendered[-_MAX_TRANSCRIPT_CHARS:]
    return rendered


async def extract_session_facts(
    messages: list[dict],
    *,
    model: Any | None = None,
) -> list[str]:
    """Propose declarative fact candidates from *messages* via a cheap model.

    Fail-soft: returns ``[]`` when no model is resolvable, the model raises, the
    call times out, or the response does not parse as the expected JSON shape.
    Mirrors the ADK async-generator contract used by ``cli/readonly_classifier``.

    The returned list is RAW candidates — the caller still runs every item
    through the declarative filter and the gated provider write.
    """
    import asyncio  # noqa: PLC0415
    import json  # noqa: PLC0415

    if model is None:
        return []

    transcript = _render_transcript(list(messages or []))
    prompt = (
        "Extract durable declarative user facts from this transcript.\n\n"
        f"{transcript}"
    )

    try:
        raw_text = await asyncio.wait_for(
            _invoke_extractor(model, prompt),
            timeout=_extract_timeout(),
        )
    except Exception:  # noqa: BLE001 — fail-soft (timeout / model error)
        return []

    return _parse_facts(raw_text, json)


async def _invoke_extractor(model: Any, prompt: str) -> str:
    """Invoke *model* via the ADK async-generator contract; collect text parts."""
    from google.adk.models.llm_request import LlmRequest  # noqa: PLC0415
    from google.genai import types  # noqa: PLC0415

    llm_request = LlmRequest(
        config=types.GenerateContentConfig(
            system_instruction=_EXTRACT_SYSTEM_INSTRUCTION,
        ),
        contents=[
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt)],
            )
        ],
    )

    collected: list[str] = []
    async for resp in model.generate_content_async(llm_request, stream=False):
        content = getattr(resp, "content", None)
        parts = getattr(content, "parts", None) if content is not None else None
        if parts:
            for part in parts:
                text = getattr(part, "text", None)
                if text:
                    collected.append(text)
    return "".join(collected)


def _parse_facts(text: str, json_module: Any) -> list[str]:
    """Parse ``{"facts": [...]}`` into a list of non-empty fact strings.

    Returns ``[]`` on any parse failure or unexpected shape (fail-soft).
    """
    stripped = (text or "").strip()
    if not stripped:
        return []
    # Strip markdown code fences if the model wrapped its JSON.
    if stripped.startswith("```"):
        inner = stripped.splitlines()
        inner = inner[1:-1] if len(inner) >= 3 else inner
        stripped = "\n".join(inner).strip()
    try:
        parsed = json_module.loads(stripped)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, dict):
        return []
    facts = parsed.get("facts")
    if not isinstance(facts, list):
        return []
    out: list[str] = []
    for item in facts:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


__all__ = [
    "Extractor",
    "MAGI_MEMORY_SESSION_EXTRACT_ENABLED_ENV",
    "MAGI_MEMORY_SESSION_EXTRACT_MODEL_ENV",
    "SessionExtractReceipt",
    "SessionExtractStatus",
    "extract_session_facts",
    "on_session_end",
]
