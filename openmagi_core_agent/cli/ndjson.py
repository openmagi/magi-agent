"""Single-writer NDJSON output for the headless CLI.

All protocol frames are funneled through ONE ``asyncio.Queue`` drained by ONE
writer coroutine, guaranteeing FIFO ordering and per-line ``flush()``. The JSON
serializer escapes U+2028 (LINE SEPARATOR) and U+2029 (PARAGRAPH SEPARATOR),
which are valid in JSON strings but break some line-oriented / JS consumers.

Logs and errors must go to stderr, never through this writer.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import IO

from pydantic import BaseModel

from openmagi_core_agent.cli.protocol import OutboundFrame

_U2028 = " "
_U2029 = " "
_U2028_ESCAPED = "\\u2028"
_U2029_ESCAPED = "\\u2029"

# Sentinel pushed onto the queue to signal the drainer to stop.
_STOP = object()


def ndjson_dumps(obj: object) -> str:
    """Serialize ``obj`` to a single JSON line with U+2028/U+2029 escaped.

    Pydantic models are dumped via ``model_dump(mode="json")``. The resulting
    string never contains a literal newline.
    """

    if isinstance(obj, BaseModel):
        payload = obj.model_dump(mode="json", exclude_none=False)
    else:
        payload = obj
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # ensure_ascii=False can emit raw U+2028/U+2029; escape them so the line is
    # safe for line-oriented and JS-string consumers.
    return text.replace(_U2028, _U2028_ESCAPED).replace(_U2029, _U2029_ESCAPED)


class NdjsonWriter:
    """FIFO single-drainer NDJSON writer.

    Frames enqueued via :meth:`write` are serialized and flushed by a single
    background drainer coroutine, preserving enqueue order. The drainer is
    started lazily on the first :meth:`write` (or via :meth:`start`).
    """

    def __init__(self, stream: IO[str] | None = None) -> None:
        self._stream: IO[str] = stream if stream is not None else sys.stdout
        self._queue: asyncio.Queue[object] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._drain_loop())

    async def write(self, frame: OutboundFrame) -> None:
        if self._closed:
            raise RuntimeError("NdjsonWriter is closed")
        await self.start()
        await self._queue.put(frame)

    async def _drain_loop(self) -> None:
        while True:
            item = await self._queue.get()
            if item is _STOP:
                self._queue.task_done()
                return
            try:
                line = ndjson_dumps(item)
                self._stream.write(line + "\n")
                self._stream.flush()
            except Exception as exc:  # noqa: BLE001
                # A per-frame failure (broken pipe, non-serializable payload)
                # must NOT kill the drainer: that would hang producers and lose
                # later frames. Log to stderr and keep draining.
                print(f"ndjson drain error: {exc!r}", file=sys.stderr, flush=True)
            finally:
                self._queue.task_done()

    async def aclose(self) -> None:
        """Drain remaining items, stop the drainer, and join cleanly."""

        if self._closed:
            return
        self._closed = True
        if self._task is None:
            # Nothing was ever written; nothing to drain.
            return
        await self._queue.put(_STOP)
        await self._task
        self._task = None


__all__ = ["NdjsonWriter", "ndjson_dumps"]
