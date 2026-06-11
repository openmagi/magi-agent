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


@dataclass(frozen=True)
class ValidatorVerdict:
    ref: str
    passed: bool
    detail: str | None = None


class ToolCtx:
    """What a ``tool`` impl receives: read args + session, a progress sink."""

    capabilities: frozenset[Capability] = frozenset({Capability.READ_SESSION})

    def __init__(self, *, tool_name: str, tool_args: Mapping[str, Any],
                 session: SessionReadView,
                 emit_progress: Callable[[str], Any] | None = None,
                 capabilities: frozenset[Capability] | None = None) -> None:
        self.tool_name = tool_name
        self.tool_args: Mapping[str, Any] = _ReadOnlyMapping(tool_args)
        self.session = session
        if capabilities is not None:
            self.capabilities = capabilities
        self._emit_progress = emit_progress

    def progress(self, message: str) -> None:
        if self._emit_progress is not None:
            self._emit_progress(message)


class ValidatorCtx:
    """A ``validator`` impl reads the produced artifact and emits a verdict.

    Phase 3 wires ``verdict()`` into ``cli/engine.py``'s live ``required_validators``
    enforce path. Phase 2 keeps it self-contained (no verifier_bus coupling)."""

    capabilities: frozenset[Capability] = frozenset(
        {Capability.READ_SESSION, Capability.EMIT_VALIDATION}
    )

    def __init__(self, *, ref: str, artifact: Mapping[str, Any],
                 session: SessionReadView,
                 capabilities: frozenset[Capability] | None = None) -> None:
        self.ref = ref
        self.artifact: Mapping[str, Any] = _ReadOnlyMapping(artifact)
        self.session = session
        if capabilities is not None:
            self.capabilities = capabilities
        self._verdict: ValidatorVerdict | None = None

    def emit(self, *, passed: bool, detail: str | None = None) -> None:
        self._verdict = ValidatorVerdict(ref=self.ref, passed=passed, detail=detail)

    def verdict(self) -> ValidatorVerdict | None:
        return self._verdict


class EvidenceProducerCtx:
    """An ``evidence_producer`` impl reads session and emits evidence records."""

    capabilities: frozenset[Capability] = frozenset(
        {Capability.READ_SESSION, Capability.EMIT_EVIDENCE}
    )

    def __init__(self, *, session: SessionReadView,
                 capabilities: frozenset[Capability] | None = None) -> None:
        self.session = session
        if capabilities is not None:
            self.capabilities = capabilities
        self._emitted: list[dict[str, Any]] = []

    def emit(self, *, evidence_type: str, payload: Mapping[str, Any]) -> None:
        self._emitted.append({"evidence_type": evidence_type, "payload": dict(payload)})

    def emitted(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._emitted)


class GatePositionViolation(ValueError):
    """A before_tool deciding impl ran with gate_position 'after' (would bypass the
    agent-level permission gate). Mirrors ControlPlane.register's footgun guard."""


def _build_session_view(adk_ctx: Any) -> SessionReadView:
    """Build a narrow read-view from an ADK ToolContext/CallbackContext duck-type."""
    state = getattr(adk_ctx, "state", None)
    state_map: Mapping[str, Any]
    try:
        state_map = dict(state) if state is not None else {}
    except TypeError:
        state_map = {}
    turn = state_map.get("turn", 0)
    return SessionReadView(
        invocation_id=str(getattr(adk_ctx, "invocation_id", "") or ""),
        agent_name=str(getattr(adk_ctx, "agent_name", "") or ""),
        turn_index=int(turn) if isinstance(turn, int) else 0,
        state=state_map,
    )


class ContextDispatcher:
    """Builds typed contexts from raw ADK args and applies impl decisions back to ADK.

    Mirrors the existing ``ControlPlane`` fan-out exactly so Phase 5 can swap the
    hand-assembled controls for registry-loaded impls with no behavior change.
    """

    def __init__(self, registry: Any) -> None:
        self._reg = registry

    def _control_entries(self) -> list[Any]:
        return self._reg.list(ptype=PrimitiveType.CONTROL_PLANE)

    def dispatch_before_tool(self, *, tool_name: str, args: dict[str, Any],
                             tool_context: Any,
                             evidence: EvidenceReadView | None = None) -> dict[str, Any] | None:
        session = _build_session_view(tool_context)
        ev = evidence or EvidenceReadView()
        for entry in self._control_entries():
            ctx = BeforeToolCtx(tool_name=tool_name, tool_args=args,
                                session=session, evidence=ev)
            entry.impl(ctx)
            decision = ctx.decision()
            if decision.action == "allow":
                continue
            # gate_position guard: a deciding before_tool impl MUST opt into
            # plugin-level execution ('before'); the default ('after'/None) preserves
            # the agent-level permission gate by forbidding the decision.
            if entry.gate_position != "before":
                raise GatePositionViolation(
                    f"control_plane:{entry.ref} decided '{decision.action}' on before_tool "
                    f"with gate_position={entry.gate_position!r}; set gate_position='before' "
                    f"to run at plugin level (this bypasses the permission gate — opt in "
                    f"explicitly) or move the decision to a later hook."
                )
            if decision.action == "deny":
                return decision.deny_result
            if decision.action == "rewrite" and decision.updated_args is not None:
                args.clear()
                args.update(decision.updated_args)
                # continue (no short-circuit on rewrite) — mirrors ControlPlane
        return None

    def dispatch_after_tool(self, *, tool_name: str, args: dict[str, Any],
                            result: Any, tool_context: Any) -> dict[str, Any] | None:
        session = _build_session_view(tool_context)
        for entry in self._control_entries():
            ctx = AfterToolCtx(tool_name=tool_name, tool_args=args,
                               result=result, session=session)
            entry.impl(ctx)
            override = ctx.override_result()
            if override is not None:
                return override  # first non-None wins
        return None

    def dispatch_before_model(self, *, callback_context: Any, llm_request: Any) -> None:
        session = _build_session_view(callback_context)
        for entry in self._control_entries():
            ctx = BeforeModelCtx(session=session)
            entry.impl(ctx)
            for role, text in ctx.pending_reinjections():
                llm_request.contents.append({"role": role, "content": text})
            if ctx.wants_clear_tools():
                cfg = getattr(llm_request, "config", None)
                if cfg is not None and getattr(cfg, "tools", None) is not None:
                    cfg.tools = []
        return None

    def dispatch_after_agent(self, *, agent_name: str, callback_context: Any) -> None:
        session = _build_session_view(callback_context)
        for entry in self._control_entries():
            ctx = AfterAgentCtx(agent_name=agent_name, session=session)
            entry.impl(ctx)
        return None


__all__ = [
    "PrimitiveType", "Capability",
    "SessionReadView", "EvidenceReadView",
    "BeforeToolCtx", "AfterToolCtx", "BeforeModelCtx", "AfterAgentCtx",
    "ToolCtx", "ValidatorCtx", "ValidatorVerdict", "EvidenceProducerCtx",
    "ContextDispatcher", "GatePositionViolation",
]
