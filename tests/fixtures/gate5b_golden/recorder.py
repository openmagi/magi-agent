"""Gate5B dispatch-trace recorder — content-addressed, order-preserving.

One event per ``Gate5BFullToolHost.dispatch`` call: tool, args digest, outcome
status+reason, and (for tools whose output is deterministic across runs) the
receipt's bounded-output digest. Mirrors neutral_runtime_golden/recorder.py.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


def _digest(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, default=str).encode()
    return "sha256:" + hashlib.sha256(blob).hexdigest()[:16]


#: Tools whose dispatch output is byte-stable across machines/runs (no clock
#: drift beyond the injected now_ms, no env-dependent notes). Bash/TestRun are
#: excluded: ``_deadline_note_safe()`` may inject an env-dependent note.
STABLE_OUTPUT_TOOLS = frozenset(
    {"Clock", "Calculation", "FileRead", "FileWrite", "FileEdit", "Glob", "Grep", "GitDiff"}
)


@dataclass
class Gate5BDispatchRecorder:
    events: list[dict[str, Any]] = field(default_factory=list)

    def record_dispatch(self, *, tool_name: str, args: dict[str, Any], outcome: Any) -> None:
        receipt = outcome.receipt
        self.events.append(
            {
                "kind": "dispatch",
                "tool": tool_name,
                "args_digest": _digest(args),
                "status": outcome.status,
                "reason": outcome.reason,
                "output_digest": (
                    receipt.bounded_output_digest
                    if tool_name in STABLE_OUTPUT_TOOLS and outcome.status == "ok"
                    else None
                ),
            }
        )


def normalize_trace(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(e) for e in events]
