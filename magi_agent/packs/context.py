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


class _ReadOnlyMapping(Mapping[str, Any]):
    """A read-only view over a dict (impls cannot mutate tool_args directly)."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data = dict(data)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


class BeforeToolCtx:
    """Capabilities: read tool_name/tool_args, read session+evidence, decide()."""

    capabilities: frozenset[Capability] = frozenset(
        {Capability.READ_SESSION, Capability.READ_EVIDENCE,
         Capability.DECIDE_TOOL, Capability.REWRITE_TOOL_ARGS}
    )

    def __init__(self, *, tool_name: str, tool_args: Mapping[str, Any],
                 session: SessionReadView, evidence: EvidenceReadView,
                 capabilities: frozenset[Capability] | None = None) -> None:
        self.tool_name = tool_name
        self.tool_args: Mapping[str, Any] = _ReadOnlyMapping(tool_args)
        self.session = session
        self.evidence = evidence
        if capabilities is not None:  # hosted may restrict; local passes full set
            self.capabilities = capabilities
        self._decision = ToolDecision(action="allow")

    def decide(self, action: Literal["allow", "deny", "rewrite"], *,
               reason: str | None = None,
               deny_result: dict[str, Any] | None = None,
               updated_args: dict[str, Any] | None = None) -> None:
        if action == "deny" and deny_result is None:
            deny_result = {"error": reason or "denied by control-plane impl"}
        self._decision = ToolDecision(
            action=action, deny_result=deny_result, updated_args=updated_args
        )

    def decision(self) -> ToolDecision:
        return self._decision


class AfterToolCtx:
    """Capabilities: read result, override() it."""

    capabilities: frozenset[Capability] = frozenset(
        {Capability.READ_SESSION, Capability.OVERRIDE_TOOL_RESULT}
    )

    def __init__(self, *, tool_name: str, tool_args: Mapping[str, Any],
                 result: Any, session: SessionReadView,
                 capabilities: frozenset[Capability] | None = None) -> None:
        self.tool_name = tool_name
        self.tool_args: Mapping[str, Any] = _ReadOnlyMapping(tool_args)
        self.result = result
        self.session = session
        if capabilities is not None:
            self.capabilities = capabilities
        self._override: dict[str, Any] | None = None

    def override(self, result: dict[str, Any]) -> None:
        self._override = result

    def override_result(self) -> dict[str, Any] | None:
        return self._override


class BeforeModelCtx:
    """Capabilities: mutate the outgoing model request via reinject()/clear_tools()."""

    capabilities: frozenset[Capability] = frozenset(
        {Capability.READ_SESSION, Capability.MUTATE_MODEL_REQUEST,
         Capability.REINJECT_MESSAGE, Capability.CLEAR_TOOLS}
    )

    def __init__(self, *, session: SessionReadView,
                 capabilities: frozenset[Capability] | None = None) -> None:
        self.session = session
        if capabilities is not None:
            self.capabilities = capabilities
        self._reinjections: list[tuple[str, str]] = []
        self._clear_tools = False

    def reinject(self, *, role: str, text: str) -> None:
        self._reinjections.append((role, text))

    def clear_tools(self) -> None:
        self._clear_tools = True

    def pending_reinjections(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._reinjections)

    def wants_clear_tools(self) -> bool:
        return self._clear_tools


class AfterAgentCtx:
    """Observe-only: a completed turn. No decision surface."""

    capabilities: frozenset[Capability] = frozenset({Capability.READ_SESSION})

    def __init__(self, *, agent_name: str, session: SessionReadView,
                 capabilities: frozenset[Capability] | None = None) -> None:
        self.agent_name = agent_name
        self.session = session
        if capabilities is not None:
            self.capabilities = capabilities
