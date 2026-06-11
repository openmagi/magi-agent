"""ControlPlaneRecorder — content-addressed decision-trace capture.

Observes control-plane decisions during a fake-model turn. The recorder stores
order-preserving, content-addressed events: tool args and after-tool overrides
are digested (not stored raw) so the golden traces are stable across runs and
free of any secret-shaped payload. Task 0.3 wires each ControlPlanePlugin /
ControlPlane fan-out decision to the matching ``record_*`` method.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


def _digest(obj: Any) -> str:
    """Stable, secret-free content address for an arbitrary JSON-able object."""
    blob = json.dumps(obj, sort_keys=True, default=str).encode()
    return "sha256:" + hashlib.sha256(blob).hexdigest()[:16]


@dataclass
class ControlPlaneRecorder:
    """Observes control-plane decisions during a fake-model turn.

    Hook each ControlPlanePlugin / ControlPlane fan-out decision to the matching
    ``record_*`` method (Task 0.3 wires this). Stores order-preserving,
    content-addressed events — stable + secret-free for golden diffing.
    """

    events: list[dict[str, Any]] = field(default_factory=list)

    def record_before_tool(
        self, *, tool_name: str, tool_args: dict, decision: dict
    ) -> None:
        self.events.append(
            {
                "kind": "before_tool",
                "tool": tool_name,
                "args_digest": _digest(tool_args),
                "decision": decision.get("action", "allow"),
                "reason": decision.get("reason"),
            }
        )

    def record_after_tool(self, *, tool_name: str, override: dict | None) -> None:
        self.events.append(
            {
                "kind": "after_tool",
                "tool": tool_name,
                "override": None if override is None else _digest(override),
            }
        )

    def record_before_model(self, *, mutated: bool, tools_cleared: bool) -> None:
        self.events.append(
            {
                "kind": "before_model",
                "mutated": bool(mutated),
                "tools_cleared": bool(tools_cleared),
            }
        )

    def record_reinject(self, *, role: str, text_digest: str, source: str) -> None:
        self.events.append(
            {
                "kind": "reinject",
                "role": role,
                "text_digest": text_digest,
                "source": source,
            }
        )

    def record_compaction(self, *, fired: bool, kept_tail: int | None) -> None:
        self.events.append(
            {"kind": "compaction", "fired": bool(fired), "kept_tail": kept_tail}
        )

    def record_tool_error(self, *, tool_name: str, override: dict | None) -> None:
        """Capture an ``on_tool_error_callback`` decision (edit-retry raise path).

        The edit-retry seam fires when a tool *raises* (not when it returns an
        error dict), so it lives at the plugin level — see
        ``_ExtendedControlPlanePlugin.on_tool_error_callback``. A non-None
        override is the corrective reflection response re-injected to the model.
        """
        self.events.append(
            {
                "kind": "tool_error",
                "tool": tool_name,
                "override": None if override is None else _digest(override),
            }
        )


def normalize_trace(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return an order-preserving deep copy; the schema is kept stable (no key drop)."""
    return [dict(e) for e in events]
