"""Production cheap-model summarizer for the compaction tree (PR2).

PR-A built :class:`~magi_agent.memory.compaction_tree.CompactionTree` with an
injectable :class:`~magi_agent.memory.compaction_tree.Summarizer` Protocol seam,
but PR-A/PR-B wired ``summarizer=None`` at every call site, so the tree always
fell back to deterministic truncation and never actually *compressed* a tier.
This module supplies the missing production implementation.

What it does
------------
:class:`CheapModelSummarizer` drives a single lightweight in-context model call
(the same ADK ``generate_content_async`` contract the SmartApprove read-only
classifier uses — see :mod:`magi_agent.cli.readonly_classifier`) to turn an
over-threshold tier text into a shorter summary.

Activation / gating
-------------------
There is NO new master flag. :func:`build_compaction_summarizer` returns a
summarizer ONLY when ``MemoryRuntimeConfig.compaction_enabled`` is True (the same
gate that decides whether the tree runs at all, default OFF). When compaction is
off it returns ``None`` and the tree keeps its inert truncation fallback. An
optional ``MAGI_MEMORY_SUMMARIZER_MODEL`` env var overrides the model id; unset
means "use the provider's default model" (the cheap tier for this provider).

Fail-open contract
------------------
The compaction tree treats a *raised* exception from ``summarize`` as a fail-open
signal: it catches it and falls back to deterministic truncation (and counts a
``summarizer_failure``). So this runtime RAISES on every unrecoverable condition
— no model/API key resolvable, the model call failing, a timeout, or empty model
output — rather than returning a partial/empty summary that would shrink a tier
to nothing. The caller (``CompactionTree._maybe_summarize``) never lets that
exception reach the turn loop.

Redaction
---------
The tier text handed to ``summarize`` is ALREADY redacted by
``CompactionTree._maybe_summarize`` (it calls ``_redact_for_write`` BEFORE the
summarizer). This runtime must NOT un-redact: it forwards the text verbatim as
the user content of the model request.
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable

from magi_agent.memory.config import MemoryRuntimeConfig

logger = logging.getLogger(__name__)

#: Optional model-id override for the compaction summarizer. Unset => the
#: provider's default (cheap) model. A ``<provider>/<model>`` slug also switches
#: the provider (mirrors ``resolve_provider_config``'s slug handling).
SUMMARIZER_MODEL_ENV_VAR: str = "MAGI_MEMORY_SUMMARIZER_MODEL"

#: Timeout (seconds) for the single summarization model call. A long tier should
#: not hang the (offloaded) compaction build; on timeout we raise and the tree
#: falls open to truncation. Reuses the SmartApprove timeout env for one knob.
SUMMARIZER_TIMEOUT_ENV_VAR: str = "MAGI_MEMORY_SUMMARIZER_TIMEOUT"
_DEFAULT_TIMEOUT_SECS: float = 30.0

#: System instruction for the summarizer. Deliberately terse + neutral so the
#: model returns prose only (no preamble), keeping the tier shrinkable.
_SYSTEM_INSTRUCTION = (
    "You compress an agent memory log into a concise summary. "
    "Preserve durable facts, decisions, identifiers, and open threads. "
    "Drop chit-chat and redundancy. Reply with ONLY the summary prose — no "
    "preamble, no headers, no code fences."
)

#: Lazily-imported builder type — a callable that turns a ProviderConfig into a
#: model object. Default is the runtime's real LiteLlm builder; tests patch it.
ModelFactory = Callable[[], object]


def _resolve_timeout() -> float:
    raw = os.environ.get(SUMMARIZER_TIMEOUT_ENV_VAR, "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return _DEFAULT_TIMEOUT_SECS


class CheapModelSummarizer:
    """Compaction summarizer backed by a single cheap-model call.

    Implements the :class:`~magi_agent.memory.compaction_tree.Summarizer`
    Protocol (a synchronous ``summarize(text) -> str``). The synchronous surface
    is deliberate: ``record_turn`` runs the whole compaction build inside
    ``asyncio.to_thread``, so this method drives the async model call on its own
    private event loop (there is no running loop on the worker thread).

    Args:
        model_factory: Test seam — a zero-arg callable returning a model object
            with the ADK ``generate_content_async`` contract. When omitted the
            model is built from a resolved ``ProviderConfig`` on first use.
        model_override: Explicit model id (``<provider>/<model>`` or bare). When
            omitted the ``MAGI_MEMORY_SUMMARIZER_MODEL`` env var is consulted;
            unset => the provider's default (cheap) model.
    """

    def __init__(
        self,
        *,
        model_factory: ModelFactory | None = None,
        model_override: str | None = None,
    ) -> None:
        self._model_factory = model_factory
        self._model_override = model_override

    # -- Summarizer protocol -------------------------------------------------

    def summarize(self, text: str) -> str:
        """Return a compressed summary of ``text`` (already redacted by caller).

        RAISES on any unrecoverable condition (no model/key, call failure,
        timeout, empty output). The compaction tree catches the raise and falls
        open to deterministic truncation, so this never reaches the turn loop.
        """
        model = self._resolve_model()
        if model is None:
            # No factory and no resolvable provider/key. Raise so the tree falls
            # open to truncation rather than silently producing nothing.
            raise RuntimeError("no model available for compaction summarizer")

        summary = self._run_summary(model, text)
        if not isinstance(summary, str) or not summary.strip():
            raise RuntimeError("compaction summarizer returned empty output")
        return summary.strip()

    # -- internals -----------------------------------------------------------

    def _resolve_model(self) -> object | None:
        """Return a model object or ``None`` (never raises — callers raise)."""
        if self._model_factory is not None:
            try:
                return self._model_factory()
            except Exception:  # noqa: BLE001 — treat as "no model" -> fall open
                logger.debug("compaction summarizer model_factory raised", exc_info=True)
                return None

        override = self._model_override
        if override is None:
            override = os.environ.get(SUMMARIZER_MODEL_ENV_VAR, "").strip() or None
        try:
            # Lazy import: keep this module free of provider/runtime deps at
            # import time (mirrors compaction_tree's import discipline).
            from magi_agent.memory.summarizer_runtime import (  # noqa: PLC0415
                _build_litellm_model,
                resolve_provider_config,
            )

            provider_config = resolve_provider_config(model_override=override)
            if provider_config is None:
                return None
            return _build_litellm_model(provider_config)
        except Exception:  # noqa: BLE001 — any build failure -> "no model"
            logger.debug("compaction summarizer model build failed", exc_info=True)
            return None

    def _run_summary(self, model: object, text: str) -> str:
        """Drive the async model call on a private event loop (sync surface)."""
        return asyncio.run(self._invoke(model, text))

    async def _invoke(self, model: object, text: str) -> str:
        return await asyncio.wait_for(
            self._collect(model, text), timeout=_resolve_timeout()
        )

    @staticmethod
    async def _collect(model: object, text: str) -> str:
        """Consume the ADK async-generator response, collecting text parts.

        Mirrors ``readonly_classifier.ReadOnlyClassifier._invoke_llm``: builds an
        ``LlmRequest`` whose single user part carries the (already-redacted) tier
        text, then accumulates every ``LlmResponse.content.parts[*].text``.
        """
        from google.adk.models.llm_request import LlmRequest  # noqa: PLC0415
        from google.genai import types  # noqa: PLC0415

        llm_request = LlmRequest(
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_INSTRUCTION,
            ),
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=text)],
                )
            ],
        )

        collected: list[str] = []
        async for resp in model.generate_content_async(llm_request, stream=False):  # type: ignore[union-attr]
            content = getattr(resp, "content", None)
            parts = getattr(content, "parts", None) if content is not None else None
            if not parts:
                continue
            for part in parts:
                part_text = getattr(part, "text", None)
                if part_text:
                    collected.append(part_text)
        return "".join(collected)


def build_compaction_summarizer(
    config: MemoryRuntimeConfig,
    *,
    model_override: str | None = None,
) -> CheapModelSummarizer | None:
    """Return a production summarizer when compaction is ON, else ``None``.

    There is NO new master flag: this gates exclusively on the same
    ``compaction_enabled`` switch that decides whether the tree runs (default
    OFF). When compaction is off the caller passes ``summarizer=None`` and the
    tree keeps its inert truncation fallback — so a clean install never
    constructs a model or reads a provider key.
    """
    if not config.compaction_enabled:
        return None
    return CheapModelSummarizer(model_override=model_override)


# Re-exported provider seam (patched by tests; kept here so the lazy import in
# ``CheapModelSummarizer._resolve_model`` resolves these names on THIS module).
def resolve_provider_config(*args, **kwargs):  # type: ignore[no-untyped-def]
    from magi_agent.cli.providers import (  # noqa: PLC0415
        resolve_provider_config as _impl,
    )

    return _impl(*args, **kwargs)


def _build_litellm_model(*args, **kwargs):  # type: ignore[no-untyped-def]
    from magi_agent.cli.real_runner import (  # noqa: PLC0415
        _build_litellm_model as _impl,
    )

    return _impl(*args, **kwargs)


__all__ = [
    "SUMMARIZER_MODEL_ENV_VAR",
    "SUMMARIZER_TIMEOUT_ENV_VAR",
    "CheapModelSummarizer",
    "build_compaction_summarizer",
]
