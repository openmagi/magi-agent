"""Bounded subprocess pipe capture shared by the sync and async shell paths.

Extracted from ``gate5b_full_toolhost`` so the native-async
:mod:`magi_agent.gates.async_shell_runner` can reuse the exact same cap +
digest + head/tail elision semantics without importing the toolhost module
(which would create an import cycle).

The semantics are byte-identical to the original ``_BoundedPipeCapture``:

* ``feed`` accumulates head (up to ~60% of the cap) and a sliding tail (~40%)
  while hashing the full raw stream.
* ``text`` returns the full decoded output when it fits the cap, else a
  head + elision marker + tail projection.
* ``digest`` returns ``_digest(text)`` of the full output when it fits the cap,
  else ``sha256:<hex of full raw stream>``.
"""
from __future__ import annotations

import hashlib
import json


def _digest(value: object) -> str:
    material = json.dumps(value, sort_keys=True, default=repr, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _decode_capture_bytes(value: bytes) -> str:
    return value.decode("utf-8", errors="replace")


class BoundedPipeCapture:
    """Bound subprocess pipe capture without buffering unbounded output."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max(1, max_bytes)
        self._raw_digest = hashlib.sha256()
        self._buffer = bytearray()
        self._tail = bytearray()
        self.total_bytes = 0

    @property
    def _head_budget(self) -> int:
        return max(1, (self.max_bytes * 3) // 5)

    @property
    def _tail_budget(self) -> int:
        return max(0, self.max_bytes - self._head_budget)

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        self.total_bytes += len(chunk)
        self._raw_digest.update(chunk)
        remaining = self.max_bytes - len(self._buffer)
        if remaining > 0:
            self._buffer.extend(chunk[:remaining])
        tail_budget = self._tail_budget
        if tail_budget > 0:
            self._tail.extend(chunk)
            overflow = len(self._tail) - tail_budget
            if overflow > 0:
                del self._tail[:overflow]

    def text(self) -> str:
        if self.total_bytes <= self.max_bytes:
            return _decode_capture_bytes(bytes(self._buffer))
        head_bytes = bytes(self._buffer[: self._head_budget])
        tail_budget = self._tail_budget
        tail_bytes = bytes(self._tail[-tail_budget:]) if tail_budget else b""
        elided = max(0, self.total_bytes - len(head_bytes) - len(tail_bytes))
        marker = (
            f"\n[... {elided} bytes elided - output truncated; re-run with "
            "head/tail/grep filters to see the elided region ...]\n"
        )
        return _decode_capture_bytes(head_bytes) + marker + _decode_capture_bytes(tail_bytes)

    def digest(self) -> str:
        if self.total_bytes <= self.max_bytes:
            return _digest(_decode_capture_bytes(bytes(self._buffer)))
        return "sha256:" + self._raw_digest.hexdigest()
