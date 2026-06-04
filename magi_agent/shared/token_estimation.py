from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Callable

__all__ = [
    "estimate_message_tokens",
    "estimate_messages_tokens",
    "count_text_tokens",
    "tokenizer_backend",
]

# Average characters-per-token for the char/4 heuristic. This is the historical
# fallback used everywhere in the runtime and stays byte-for-byte identical to
# ``len(json.dumps(...)) // 4`` so existing budgets/thresholds do not shift when
# no real tokenizer is installed.
_CHARS_PER_TOKEN = 4


@lru_cache(maxsize=1)
def _load_tiktoken_encoder() -> Callable[[str], int] | None:
    """Best-effort load of an optional ``tiktoken`` encoder.

    Returns a ``str -> int`` token counter, or ``None`` when ``tiktoken`` is not
    installed or fails to initialise. ``tiktoken`` is an *optional* dependency —
    it is never a hard requirement, so any import/initialisation failure degrades
    silently to the char/4 heuristic. The encoder is cached so the (relatively
    expensive) BPE table load happens at most once per process.
    """
    try:  # pragma: no cover - exercised only when tiktoken is installed
        import tiktoken  # type: ignore[import-not-found]
    except Exception:
        return None
    try:  # pragma: no cover - exercised only when tiktoken is installed
        # ``cl100k_base`` is a stable, broadly-representative BPE encoding. We do
        # not pin a model name (which can require a network fetch); the base
        # encoding ships with the package and loads offline.
        encoder = tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None

    def _count(text: str) -> int:  # pragma: no cover - tiktoken-only path
        # ``disallowed_special=()`` is load-bearing: by default ``encode`` RAISES
        # on text containing ``<|...|>``-style special-token sequences (which can
        # appear verbatim in tool output / transcripts). Allowing them keeps the
        # counter total — without this, a raise here would fail-open and silently
        # disable compaction for any context whose text contains such a sequence.
        return len(encoder.encode(text, disallowed_special=()))

    return _count


def tokenizer_backend() -> str:
    """Return the active token-estimation backend name.

    ``"tiktoken"`` when the optional encoder is available, otherwise
    ``"char_heuristic"``. Intended for diagnostics/tests, not hot-path use.
    """
    return "tiktoken" if _load_tiktoken_encoder() is not None else "char_heuristic"


def count_text_tokens(text: str) -> int:
    """Best-effort token count for a raw string.

    Uses an optional ``tiktoken`` encoder when present (real BPE token counts),
    and otherwise falls back to the historical ``len // 4`` character heuristic.
    Pure and fast: no I/O on the hot path (the encoder, if any, is cached).
    """
    if not text:
        return 0
    encoder = _load_tiktoken_encoder()
    if encoder is not None:  # pragma: no cover - tiktoken-only path
        return encoder(text)
    return len(text) // _CHARS_PER_TOKEN


def estimate_message_tokens(message: dict[str, object]) -> int:
    """Estimate token count for a single message dict.

    Backward-compatible: when no real tokenizer is installed this is byte-for-byte
    identical to the historical ``len(json.dumps(msg)) // 4`` approximation, so
    every existing caller/threshold keeps the same numbers. When the optional
    ``tiktoken`` dependency is present, the same JSON serialisation is tokenised
    with a real BPE encoder for a materially more accurate estimate.
    """
    serialized = json.dumps(message, default=str)
    encoder = _load_tiktoken_encoder()
    if encoder is not None:  # pragma: no cover - tiktoken-only path
        return encoder(serialized)
    return len(serialized) // _CHARS_PER_TOKEN


def estimate_messages_tokens(messages: list[dict[str, object]]) -> int:
    """Estimate total tokens for a list of messages."""
    return sum(estimate_message_tokens(m) for m in messages)
