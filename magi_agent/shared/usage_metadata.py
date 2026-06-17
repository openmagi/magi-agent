"""Shared, duck-typed ADK usage-metadata extraction (single source).

The CLI engine (``magi_agent.cli.engine``) and the live context-compaction plugin
(``magi_agent.adk_bridge.context_compaction``) both need to read token counts off
a raw ADK event / ``LlmResponse``. ADK's ``usage_metadata`` is provider-shaped
(``prompt_token_count`` / ``promptTokenCount`` etc.) and may sit either directly
on the object or nested under ``llm_response`` / ``response``.

This module hosts the duck-typed readers so:

* ``engine.py`` reuses them WITHOUT naming any ``google.*`` symbol at module
  scope (asserted by ``test_engine_import_clean_in_fresh_interpreter``). Hence
  this module imports **no** ``google.*`` at module scope either.
* the compaction plugin reads the real prompt-token count of the just-completed
  model call through the same, already-hardened extraction logic.

All readers are fail-open: a hostile/odd object yields ``None`` rather than
raising into a caller on the live model loop.
"""

from __future__ import annotations

from collections.abc import Mapping


def usage_member(value: object, *names: str) -> object:
    """First non-None ``name`` read from a Mapping (``.get``) or object (``getattr``).

    Duck-typed: never raises on a hostile attribute descriptor.
    """
    for name in names:
        if isinstance(value, Mapping):
            found = value.get(name)
        else:
            try:
                found = getattr(value, name, None)
            except Exception:  # noqa: BLE001 - duck-typed read must never raise
                found = None
        if found is not None:
            return found
    return None


def usage_int(meta: object, *names: str) -> int | None:
    """First non-negative ``int`` among ``names`` (bools rejected)."""
    for name in names:
        value = usage_member(meta, name)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value >= 0:
            return value
    return None


def adk_usage_metadata(event: object, *, depth: int = 0) -> dict[str, int] | None:
    """Token usage from a raw ADK event as canonical snake_case keys, or ``None``.

    Duck-typed (``getattr``/``Mapping`` only) so callers name no ``google.*``
    symbol at module scope. Mirrors the shadow twin
    ``shadow/gate5b4c3_live_runner_boundary.py:_event_usage_metadata``.

    ADK ``usage_metadata`` is cumulative WITHIN one ``run_async`` stream, so callers
    last-writer-wins within a stream and SUM across re-invocations. Zero/missing
    counts are omitted (never fabricated); ``total_tokens`` is taken verbatim from
    ``total_token_count`` only when the provider supplies it.
    """
    if depth > 3:
        return None
    meta = usage_member(event, "usage_metadata", "usageMetadata")
    if meta is not None:
        prompt = usage_int(meta, "prompt_token_count", "promptTokenCount")
        candidates = usage_int(meta, "candidates_token_count", "candidatesTokenCount")
        cached = usage_int(meta, "cached_content_token_count", "cachedContentTokenCount")
        total = usage_int(meta, "total_token_count", "totalTokenCount")
        result: dict[str, int] = {}
        if prompt:
            result["input_tokens"] = prompt
        if candidates:
            result["output_tokens"] = candidates
        if cached:
            result["cache_read_tokens"] = cached
        if total:
            result["total_tokens"] = total
        if result:
            return result
    for nested_name in ("llm_response", "response"):
        nested = usage_member(event, nested_name)
        if nested is not None:
            found = adk_usage_metadata(nested, depth=depth + 1)
            if found is not None:
                return found
    return None


def prompt_tokens_from_response(response: object) -> int | None:
    """Real prompt-token count from an ADK ``LlmResponse``/event, or ``None``.

    Reuses :func:`adk_usage_metadata` (the same hardened extraction the engine
    uses) and returns only its ``input_tokens`` (= ``prompt_token_count``). A
    missing/zero/non-int prompt count yields ``None`` so the caller fails open to
    its estimate path. Never raises.
    """
    try:
        usage = adk_usage_metadata(response)
    except Exception:  # noqa: BLE001 - defensive; extraction is already fail-open
        return None
    if usage is None:
        return None
    value = usage.get("input_tokens")
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


__all__ = [
    "usage_member",
    "usage_int",
    "adk_usage_metadata",
    "prompt_tokens_from_response",
]
