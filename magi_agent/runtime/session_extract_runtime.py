"""Glue that runs session-end memory extraction at real session boundaries.

PR4 (``harness/memory_session_extract.on_session_end``) shipped the extraction
logic but no caller. This module is the thin runtime glue that wires it to the
two boundaries where a transcript is actually reachable:

* **CLI / headless run end** (``cli/headless.run_headless``) — the headless
  invocation IS a bounded session; when it returns, the session ends.
* **serve process shutdown** (``app.lifespan`` finally) — the long-lived local
  server has no per-conversation end signal, so its active ADK sessions are
  flushed once, best-effort, on shutdown.

There is no real ``/reset`` boundary to hook (the slash ``/reset`` is intent-only
and the actual session-reset path is unbuilt), so reset is intentionally not a
trigger here.

GOVERNANCE
----------
* **Default OFF** behind ``MAGI_MEMORY_SESSION_EXTRACT_ENABLED``. When off this
  module short-circuits with NO model build, NO session iteration, NO write.
* **Writes stay gated** by the existing memory write gate, owned by
  ``LocalFileMemoryProvider`` (which also redacts, bounds, and pins the target to
  ``MEMORY.md`` — the agent can never reach ``SOUL.md``).
* **Fail-soft**: every path swallows its own errors. Nothing here may raise into
  the caller's exit / shutdown path.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Optional cheap-model override for the session-end extractor. Unset => the
#: provider's default (cheap) model, resolved via the same builder the compaction
#: summarizer uses. A ``<provider>/<model>`` slug also switches provider.
SESSION_EXTRACT_MODEL_ENV_VAR = "MAGI_MEMORY_SESSION_EXTRACT_MODEL"

_TRUTHY = {"1", "true", "yes", "on"}


def session_extract_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return whether session-end extraction is enabled (default OFF)."""
    from magi_agent.harness.memory_session_extract import (  # noqa: PLC0415
        MAGI_MEMORY_SESSION_EXTRACT_ENABLED_ENV,
    )

    source = os.environ if env is None else env
    raw = str(source.get(MAGI_MEMORY_SESSION_EXTRACT_ENABLED_ENV, "")).strip().lower()
    return raw in _TRUTHY


def _build_extract_model() -> Any | None:
    """Build the cheap extractor model, reusing the compaction summarizer's
    provider/LiteLlm builder. Returns ``None`` (never raises) when no provider or
    key is resolvable, so the extractor degrades to a no-op."""
    try:
        from magi_agent.memory.summarizer_runtime import (  # noqa: PLC0415
            _build_litellm_model,
            resolve_provider_config,
        )

        override = os.environ.get(SESSION_EXTRACT_MODEL_ENV_VAR, "").strip() or None
        provider_config = resolve_provider_config(model_override=override)
        if provider_config is None:
            return None
        return _build_litellm_model(provider_config)
    except Exception:  # noqa: BLE001 — any build failure -> "no model" (no-op extract)
        logger.debug("session-extract model build failed", exc_info=True)
        return None


def _build_provider(workspace_root: Path | str) -> Any:
    from magi_agent.memory.adapters.local_file_writable import (  # noqa: PLC0415
        LocalFileMemoryConfig,
        LocalFileMemoryProvider,
    )

    config = LocalFileMemoryConfig(workspace_root=Path(workspace_root), enabled=True)
    return LocalFileMemoryProvider(config)


async def run_session_extract(
    messages: list[dict],
    *,
    workspace_root: Path | str,
    model: Any | None = None,
) -> Any | None:
    """Run session-end extraction for one transcript. Gated + fail-soft.

    Returns the ``SessionExtractReceipt`` (or ``None`` when gated off, the
    transcript is empty, or anything failed). When ``model`` is omitted a cheap
    model is built lazily; if none is resolvable the extractor yields no facts.
    """
    if not session_extract_enabled() or not messages:
        return None
    try:
        from magi_agent.harness.memory_session_extract import (  # noqa: PLC0415
            on_session_end,
        )

        provider = _build_provider(workspace_root)
        resolved_model = model if model is not None else _build_extract_model()
        return await on_session_end(messages, provider=provider, model=resolved_model)
    except Exception:  # noqa: BLE001 — never raise into the caller's exit path
        logger.debug("session-extract flush failed (ignored)", exc_info=True)
        return None


def _adk_event_text(event: Any) -> str:
    """Concatenate the text parts of an ADK event's content (text-only)."""
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None) if content is not None else None
    if not parts:
        return ""
    return "".join(
        part.text for part in parts if isinstance(getattr(part, "text", None), str)
    )


def messages_from_adk_events(events: Any) -> list[dict]:
    """Convert ADK session events to the ``[{role, content}]`` transcript shape.

    Text-only: tool / function-call events without text are skipped. ``author``
    ``"user"`` maps to the user role; every other author maps to assistant. Order
    is preserved.
    """
    out: list[dict] = []
    for event in events or []:
        text = _adk_event_text(event)
        if not text.strip():
            continue
        author = (getattr(event, "author", "") or "").strip().lower()
        role = "user" if author == "user" else "assistant"
        out.append({"role": role, "content": text})
    return out


__all__ = [
    "SESSION_EXTRACT_MODEL_ENV_VAR",
    "messages_from_adk_events",
    "run_session_extract",
    "session_extract_enabled",
]
