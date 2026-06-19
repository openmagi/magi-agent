"""Per-session inject buffer shared by chat-routes and the background-task sink.

The web/serve gate5b live-runner boundary is a one-shot JSONResponse and cannot
deliver a message mid-turn. The honest behaviour is queue-to-next-turn: append
to a session-keyed buffer and have the next prompt-assembly drain it.

Originally lived inside ``transport/chat_routes.py`` as
``_INJECT_BUFFERS``/``_buffer_injection`` (append-only, no consumer). Moved here
so the background-task completion sink (in the gateway/work-queue layer) can
write into the same buffer without importing ``transport.chat_routes`` (which
would pull FastAPI into the queue layer). The consumer side stays in
``chat_routes`` and reads via ``drain``.

Process-local, asyncio-loop-only (same constraints as the original module-level
dict; one serve process per event loop).
"""

from __future__ import annotations


_BUFFERS: dict[str, list[str]] = {}


def enqueue(session_id: str, text: str) -> int:
    """Append *text* to *session_id*'s pending-injection buffer; return its size.

    Blank / whitespace-only text is a no-op so callers can pass raw summaries
    without pre-trimming.
    """
    if not isinstance(text, str) or not text.strip():
        return len(_BUFFERS.get(session_id, ()))
    bucket = _BUFFERS.setdefault(session_id, [])
    bucket.append(text)
    return len(bucket)


def drain(session_id: str) -> list[str]:
    """Return *session_id*'s pending injections and clear them. Idempotent."""
    bucket = _BUFFERS.pop(session_id, None)
    return list(bucket) if bucket else []


def peek_size(session_id: str) -> int:
    return len(_BUFFERS.get(session_id, ()))


def reset_for_tests() -> None:
    _BUFFERS.clear()


__all__ = ["enqueue", "drain", "peek_size", "reset_for_tests"]
