"""PR3 — optional cheap-model semantic re-rank over BM25 recall candidates.

The per-turn recall path (:func:`magi_agent.cli.memory_recall_block.build_cli_memory_recall_block`)
ranks memory hits with BM25 (lexical only).  This module adds an OPTIONAL layer:
given the BM25 candidate hits plus a manifest of the memory tree, a cheap model
re-orders the candidates by semantic relevance to the query.

GOVERNANCE INVARIANTS
---------------------
* **Default OFF** behind ``MAGI_MEMORY_RECALL_RERANK_ENABLED``.  When the flag is
  off, :func:`rerank_hits` returns the input hits UNCHANGED (identity) — the
  recall block is byte-identical to the pre-PR3 BM25 order.
* **Fail-open**: ANY error (no model / no key / selector exception / unparseable
  response) returns the original BM25 order.  Re-rank never raises into the turn
  loop and never *drops* a candidate — it can only reorder a subset and append
  the remainder in their original BM25 order.
* **No new deps / no network in tests**: the model is built lazily via the same
  ``ProviderConfig`` → LiteLlm builder the SmartApprove classifier uses; tests
  inject a fake ``model_factory``.

The selector is given a compact, in-context listing (rank, path, manifest
description/type, snippet) and must reply with a JSON array of the chosen path
order, e.g. ``{"order": ["memory/daily/b.md", "memory/daily/a.md"]}``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from magi_agent.cli.memory_manifest import build_memory_manifest

if TYPE_CHECKING:
    from magi_agent.memory.search.base import SearchHit

logger = logging.getLogger(__name__)

__all__ = [
    "MAGI_MEMORY_RECALL_RERANK_ENABLED_ENV",
    "rerank_hits",
]

#: Default-OFF activation flag for the cheap-model re-rank layer.
MAGI_MEMORY_RECALL_RERANK_ENABLED_ENV = "MAGI_MEMORY_RECALL_RERANK_ENABLED"

#: Model env override — lets a faster/cheaper model do the re-rank.
_ENV_MODEL_OVERRIDE = "MAGI_MEMORY_RECALL_RERANK_MODEL"

#: Timeout (seconds) for the selector call.
_ENV_TIMEOUT_OVERRIDE = "MAGI_MEMORY_RECALL_RERANK_TIMEOUT"
_DEFAULT_TIMEOUT_SECS = 10.0

#: Per-candidate snippet length handed to the selector (untrusted body — capped).
_MAX_SNIPPET_CHARS = 400


_RERANK_PROMPT_TEMPLATE = """\
You are a memory re-ranker. Given the user query and a list of candidate memory
documents (already pre-filtered by keyword search), order them from MOST to LEAST
relevant to answering the query. Do NOT add or remove documents.

User query:
{query}

Candidates:
{candidates}

Reply with ONLY a JSON object, no other text:
{{"order": ["<path>", "<path>", ...]}}
"""


def _rerank_gate_open(env: "os._Environ[str] | dict[str, str] | None" = None) -> bool:
    source = os.environ if env is None else env
    # I-1: route through the typed flag registry. ``flag_bool`` returns
    # ``False`` on unset (matches the prior ``in _TRUE_VALUES`` check on
    # ``""``); ``TRUE_VALUES`` (canonical) matches the local
    # ``_TRUE_VALUES`` literal ``{"1", "true", "yes", "on"}``.
    from magi_agent.config.flags import flag_bool  # noqa: PLC0415

    return flag_bool(MAGI_MEMORY_RECALL_RERANK_ENABLED_ENV, env=source)


def rerank_hits(
    *,
    hits: "Sequence[SearchHit]",
    query: str,
    memory_dir: Path,
    config: object,
    model_factory: Callable[[], object] | None = None,
) -> "list[SearchHit]":
    """Return ``hits`` re-ordered by a cheap model, or unchanged (fail-open).

    Identity (input order, byte-for-byte downstream) when the gate is OFF, fewer
    than two candidates, or any failure in model resolution / invocation /
    parsing.  Never raises; never drops a candidate.
    """
    ordered = list(hits)
    if len(ordered) < 2 or not _rerank_gate_open():
        return ordered
    try:
        model = _resolve_model(config, model_factory)
        if model is None:
            return ordered
        manifest = _manifest_by_path(memory_dir)
        prompt = _build_prompt(query=query, hits=ordered, manifest=manifest)
        raw = asyncio.run(
            asyncio.wait_for(_invoke_llm(model, prompt), timeout=_resolve_timeout())
        )
        order = _parse_order(raw)
        if not order:
            return ordered
        return _apply_order(ordered, order)
    except Exception:  # noqa: BLE001 — fail-open: never break the recall path
        logger.debug("Memory recall re-rank failed; using BM25 order", exc_info=True)
        return ordered


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _manifest_by_path(memory_dir: Path) -> dict[str, object]:
    try:
        return {entry.path: entry for entry in build_memory_manifest(memory_dir)}
    except Exception:  # noqa: BLE001
        return {}


def _build_prompt(
    *,
    query: str,
    hits: "Sequence[SearchHit]",
    manifest: dict[str, object],
) -> str:
    lines: list[str] = []
    for rank, hit in enumerate(hits):
        rel = _hit_rel_path(hit.path, manifest)
        entry = manifest.get(rel) or manifest.get(hit.path)
        desc = getattr(entry, "description", "") if entry is not None else ""
        kind = getattr(entry, "type", "") if entry is not None else ""
        snippet = " ".join(str(hit.content).split())[:_MAX_SNIPPET_CHARS]
        meta = " ".join(part for part in (kind, desc) if part)
        lines.append(
            f"{rank}. path={hit.path}"
            + (f" [{meta}]" if meta else "")
            + f"\n   {snippet}"
        )
    return _RERANK_PROMPT_TEMPLATE.format(query=query, candidates="\n".join(lines))


def _hit_rel_path(path: str, manifest: dict[str, object]) -> str:
    """Best-effort map a hit's (workspace-relative) path to a manifest key.

    BM25 hit paths are workspace-relative (e.g. ``memory/daily/x.md``) while the
    manifest is keyed relative to the ``memory/`` dir (``daily/x.md``).  Try the
    ``memory/``-stripped form so the description/type can be attached.
    """
    if path in manifest:
        return path
    stripped = path[len("memory/") :] if path.startswith("memory/") else path
    return stripped


def _apply_order(hits: "list[SearchHit]", order: list[str]) -> "list[SearchHit]":
    """Reorder ``hits`` to follow ``order`` (by path), appending any unmatched
    candidates in their original BM25 order.  Never drops or duplicates."""
    by_path: dict[str, "SearchHit"] = {}
    for hit in hits:
        by_path.setdefault(hit.path, hit)
    result: "list[SearchHit]" = []
    used: set[str] = set()
    for path in order:
        hit = by_path.get(path)
        if hit is not None and path not in used:
            result.append(hit)
            used.add(path)
    for hit in hits:
        if hit.path not in used:
            result.append(hit)
            used.add(hit.path)
    return result


def _parse_order(text: str) -> list[str]:
    text = (text or "").strip()
    if text.startswith("```"):
        inner = text.splitlines()
        text = "\n".join(inner[1:-1] if len(inner) >= 3 else inner).strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    if isinstance(parsed, dict):
        parsed = parsed.get("order")
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if isinstance(item, str)]


def _resolve_model(
    config: object,
    model_factory: Callable[[], object] | None,
) -> object | None:
    if model_factory is not None:
        try:
            return model_factory()
        except Exception:  # noqa: BLE001
            return None
    try:
        from magi_agent.cli.readonly_classifier import (  # noqa: PLC0415
            _build_litellm_for_config,
        )

        model_override = os.environ.get(_ENV_MODEL_OVERRIDE, "").strip() or None
        return _build_litellm_for_config(config, model_override=model_override)
    except Exception:  # noqa: BLE001
        return None


def _resolve_timeout() -> float:
    raw = os.environ.get(_ENV_TIMEOUT_OVERRIDE, "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return _DEFAULT_TIMEOUT_SECS


async def _invoke_llm(model: object, prompt: str) -> str:
    """Invoke the model via the ADK async-generator contract (mirrors
    ``readonly_classifier._invoke_llm``)."""
    from google.adk.models.llm_request import LlmRequest  # noqa: PLC0415
    from google.genai import types  # noqa: PLC0415

    llm_request = LlmRequest(
        config=types.GenerateContentConfig(
            system_instruction=(
                "You are a memory re-ranker. Reply with ONLY a JSON object: "
                '{"order": ["<path>", ...]}'
            ),
        ),
        contents=[
            types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
        ],
    )
    collected: list[str] = []
    async for resp in model.generate_content_async(llm_request, stream=False):  # type: ignore[union-attr]
        if resp.content and resp.content.parts:
            for part in resp.content.parts:
                if part.text:
                    collected.append(part.text)
    return "".join(collected)
