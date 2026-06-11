"""D5 typed-context ABI + dispatcher for the neutral microkernel.

Each primitive impl receives ONLY a narrow, typed, read-mostly context exposing
exactly its type's capabilities. First-party and user impls receive the SAME
object (no privileged handle). Contexts carry a frozen ``capabilities`` set that
is NOT gated in full-trust local (D6) but reserves the seam for a hosted build
to restrict capability without changing any impl signature.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

# Reuse the existing decision type — do NOT redefine.
from magi_agent.adk_bridge.control_plane import ToolDecision


class PrimitiveType(str, Enum):
    """The 8 unified ``provides`` types (D2)."""

    TOOL = "tool"
    CALLBACK = "callback"
    VALIDATOR = "validator"
    HARNESS = "harness"
    CONTROL_PLANE = "control_plane"
    EVIDENCE_PRODUCER = "evidence_producer"
    RECIPE = "recipe"
    CONNECTOR = "connector"


class Capability(str, Enum):
    """Opaque capability tokens. Full-trust local does not enforce these; a hosted
    build can later pass a restricted set per impl WITHOUT changing signatures."""

    READ_SESSION = "read_session"
    READ_EVIDENCE = "read_evidence"
    DECIDE_TOOL = "decide_tool"
    REWRITE_TOOL_ARGS = "rewrite_tool_args"
    OVERRIDE_TOOL_RESULT = "override_tool_result"
    MUTATE_MODEL_REQUEST = "mutate_model_request"
    REINJECT_MESSAGE = "reinject_message"
    CLEAR_TOOLS = "clear_tools"
    EMIT_VALIDATION = "emit_validation"
    EMIT_EVIDENCE = "emit_evidence"
    SPAWN_AGENT = "spawn_agent"

    @classmethod
    def all_tokens(cls) -> frozenset["Capability"]:
        return frozenset(cls)


@dataclass(frozen=True)
class SessionReadView:
    """Narrow, frozen projection of the ADK session for read-only impl access."""

    invocation_id: str
    agent_name: str
    turn_index: int
    _state: Mapping[str, Any] = field(default_factory=dict)

    def __init__(self, *, invocation_id: str, agent_name: str, turn_index: int,
                 state: Mapping[str, Any] | None = None) -> None:
        object.__setattr__(self, "invocation_id", invocation_id)
        object.__setattr__(self, "agent_name", agent_name)
        object.__setattr__(self, "turn_index", turn_index)
        # snapshot copy — never alias the live ADK state dict
        object.__setattr__(self, "_state", dict(state or {}))

    def get_state(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    def state_keys(self) -> tuple[str, ...]:
        return tuple(self._state.keys())


@dataclass(frozen=True)
class EvidenceReadView:
    """Read-only view of evidence already present and still owed this turn."""

    present: tuple[str, ...] = ()
    owed: tuple[str, ...] = ()

    def has(self, evidence_type: str) -> bool:
        return evidence_type in self.present
