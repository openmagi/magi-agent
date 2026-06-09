"""Stable interface surface for the Magi headless CLI.

Downstream streams (B/C/D/E/F) import ONLY from this module. The names here are
load-bearing — do not rename them without coordinating every stream.

Design constraints:
- Zero heavy deps. No ``textual``, no ``google-adk``, no ``rich`` imports.
- Import-safe with no event-loop side effects (importing this module never
  creates or touches an asyncio loop).
- We re-export ``RuntimeEvent`` (the engine event type) from
  ``magi_agent.runtime.events`` so consumers write
  ``from magi_agent.cli.contracts import RuntimeEvent``.

EngineDriver / AsyncGenerator terminal-result convention
--------------------------------------------------------
An ``async def`` generator in Python CANNOT ``return value`` (it is a
``SyntaxError`` / the value is dropped), so we cannot deliver the terminal
``EngineResult`` via a generator return. The ``EngineDriver.run_turn_stream``
Protocol is annotated ``AsyncGenerator[RuntimeEvent, EngineResult]`` to express
the intended "stream of events, then a terminal result" contract (mirroring a
TypeScript ``AsyncGenerator<Event, Terminal>``).

The agreed CONSUMPTION CONVENTION is:

    The driver yields ``RuntimeEvent`` objects, and the terminal
    ``EngineResult`` is delivered as the FINAL yielded item — i.e. the last
    object produced by ``async for`` is an ``EngineResult`` instance, not a
    ``RuntimeEvent``. Consumers iterate and, on encountering an
    ``EngineResult``, stop and treat it as terminal.

The drain logic is centralized in ``headless.drain`` so every stream consumes
the driver identically.
"""

from __future__ import annotations

import abc
import asyncio
import enum
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol, Union, runtime_checkable

from magi_agent.runtime.control import ControlRequest
from magi_agent.runtime.events import RuntimeEvent

__all__ = [
    # terminal + engine
    "Terminal",
    "RuntimeEvent",
    "EngineResult",
    "EngineDriver",
    "TurnInput",
    # permissions
    "RuleVerdict",
    "PermissionUpdate",
    "PermissionDecision",
    "PromptSink",
    "PermissionGate",
    "NullPermissionGate",
    # rendering
    "RenderNode",
    "ToolRenderer",
    "ToolRendererRegistry",
    # commands
    "CommandSurface",
    "CommandContext",
    "EmitFn",
    "ContentBlock",
    "LocalResult",
    "Text",
    "Compact",
    "Skip",
    "PromptCommand",
    "LocalCommand",
    "WidgetCommand",
    "WidgetDone",
    "Command",
    "CommandExecutor",
    "CommandRegistry",
    # re-exported runtime type
    "ControlRequest",
]


# ---------------------------------------------------------------------------
# Terminal + engine
# ---------------------------------------------------------------------------
class Terminal(enum.Enum):
    """How a turn finished. Values equal member names for stable JSON."""

    completed = "completed"
    aborted = "aborted"
    max_turns = "max_turns"
    error = "error"


@dataclass
class EngineResult:
    """Terminal result of a turn, delivered as the final yielded item.

    See module docstring for the ``AsyncGenerator[RuntimeEvent, EngineResult]``
    consumption convention.

    ``session_id`` / ``turn_id`` are additive, defaulted fields appended AFTER
    ``error`` so existing positional/keyword construction
    (``EngineResult(terminal=..., usage=..., cost_usd=..., error=...)``) stays
    valid. They let Stream B treat a terminal as a self-contained envelope.
    """

    terminal: Terminal
    usage: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    error: str | None = None
    session_id: str | None = None
    turn_id: str | None = None


@dataclass
class TurnInput:
    """Typed turn input the engine accepts (alongside a bare dict for back-compat).

    ``initial_messages`` is a reserved seam for PR3/Stream B session resume (full
    rehydration via ``SessionContinuityBoundary``); ``harness_state`` is threaded
    into the runner today.
    """

    prompt: str = ""
    session_id: str = "cli-session"
    turn_id: str = "cli-turn"
    initial_messages: list = field(default_factory=list)
    harness_state: object | None = None


@runtime_checkable
class EngineDriver(Protocol):
    """Drives a single turn, producing a stream of events + a terminal result.

    The return annotation ``AsyncGenerator[RuntimeEvent, EngineResult]``
    encodes the intended contract, but per the module's consumption convention
    the terminal ``EngineResult`` is the FINAL yielded item (NOT a generator
    return value — ``async def`` generators cannot ``return`` a value).
    """

    def run_turn_stream(
        self,
        runtime: object,
        turn_input: object,
        *,
        cancel: asyncio.Event,
        gate: "PermissionGate | None" = None,
    ) -> AsyncGenerator[RuntimeEvent, EngineResult]:
        # `import asyncio` is import-side-effect-free (it does NOT create a loop),
        # so annotating `cancel` precisely costs nothing and strengthens the
        # contract for every downstream stream.
        #
        # ``gate`` is a DI seam for Stream C's permission gate; default None = no
        # permission interception (today's behavior). Stream C/F wire the real
        # flow. This is just the seam — no permission logic lives here.
        ...


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
RuleVerdict = Literal["allow", "deny", "ask"]


@dataclass
class PermissionUpdate:
    """A remember-rule produced by a permission decision."""

    tool: str
    matcher: str
    decision: str  # one of RuleVerdict values: "allow" | "deny" | "ask"


@dataclass
class PermissionDecision:
    kind: Literal["allow", "deny"]
    updates: list[PermissionUpdate] = field(default_factory=list)
    updated_input: dict | None = None
    feedback: str | None = None
    interrupt: bool = False


@runtime_checkable
class PromptSink(Protocol):
    """Asks an external surface (e.g. the TUI / Stream C) for a decision."""

    async def ask(self, req: ControlRequest) -> PermissionDecision: ...


class PermissionGate(abc.ABC):
    """Abstract permission gate; subclasses decide on a ControlRequest."""

    @abc.abstractmethod
    async def check(self, req: ControlRequest) -> PermissionDecision: ...


class NullPermissionGate(PermissionGate):
    """Rules-less gate used to make headless testable without Stream C.

    With ``allow_in_test=True`` it allows everything. Otherwise it models the
    ``ask`` -> ``deny`` fallback (no interactive surface to answer, so deny).
    """

    def __init__(self, *, allow_in_test: bool = False) -> None:
        self.allow_in_test = allow_in_test

    async def check(self, req: ControlRequest) -> PermissionDecision:
        _ = req
        if self.allow_in_test:
            return PermissionDecision(kind="allow")
        return PermissionDecision(kind="deny")


# ---------------------------------------------------------------------------
# Rendering (no `rich` import — `rich` typed as object|None)
# ---------------------------------------------------------------------------
@dataclass
class RenderNode:
    rich: object | None = None
    text: str = ""


@runtime_checkable
class ToolRenderer(Protocol):
    def render_call(self, partial_input: object) -> RenderNode: ...
    def render_result(self, result: object) -> RenderNode: ...
    def render_progress(self, p: object) -> RenderNode: ...
    def render_rejected(self, r: object) -> RenderNode: ...
    def extract_search_text(self, node: object) -> str: ...


class _FallbackToolRenderer:
    """Minimal default renderer used when no tool-specific renderer exists."""

    def render_call(self, partial_input: object) -> RenderNode:
        return RenderNode(text=str(partial_input))

    def render_result(self, result: object) -> RenderNode:
        return RenderNode(text=str(result))

    def render_progress(self, p: object) -> RenderNode:
        return RenderNode(text=str(p))

    def render_rejected(self, r: object) -> RenderNode:
        return RenderNode(text=str(r))

    def extract_search_text(self, node: object) -> str:
        if isinstance(node, RenderNode):
            return node.text
        return str(node)


class ToolRendererRegistry:
    """Maps tool-name -> ToolRenderer with a default fallback renderer."""

    def __init__(self, *, fallback: ToolRenderer | None = None) -> None:
        self._renderers: dict[str, ToolRenderer] = {}
        self._fallback: ToolRenderer = fallback or _FallbackToolRenderer()

    def register(self, name: str, renderer: ToolRenderer) -> None:
        self._renderers[name] = renderer

    def get(self, name: str) -> ToolRenderer:
        return self._renderers.get(name, self._fallback)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
# Async event-emit callback a command uses to push a RuntimeEvent back into the
# surface's stream. Typed as a precise alias so every stream agrees on the shape.
EmitFn = Callable[[RuntimeEvent], Awaitable[None]]


@dataclass
class ContentBlock:
    """A minimal model prompt content block produced by a ``PromptCommand``."""

    type: str = "text"
    text: str = ""


@dataclass
class CommandSurface:
    tui: bool
    headless: bool


@dataclass
class CommandContext:
    cwd: str
    session: object | None = None
    runtime: object | None = None
    emit: "EmitFn | None" = None
    # App-facing opener seam (TUI). ``app`` exposes start_turn / commit_text /
    # request_compact / open_dialog. Kept as ``object | None`` so contracts.py
    # never imports textual; the TUI app structurally satisfies it. Additive,
    # defaulted last so existing positional/keyword construction stays valid.
    app: object | None = None


# Local command result union.
@dataclass
class Text:
    text: str


@dataclass
class Compact:
    pass


@dataclass
class Skip:
    pass


LocalResult = Union[Text, Compact, Skip]


@dataclass
class PromptCommand:
    """A slash-command that expands into model prompt content blocks.

    Shape: a base dataclass with an overridable coroutine ``build_prompt``.
    Stream D subclasses this and overrides ``build_prompt`` to return a list of
    content blocks. The default returns an empty block list.
    """

    name: str
    surface: CommandSurface

    async def build_prompt(
        self, args: object, ctx: CommandContext
    ) -> list[ContentBlock]:
        _ = (args, ctx)
        return []


@dataclass
class LocalCommand:
    """A slash-command handled locally (no model round-trip)."""

    name: str
    surface: CommandSurface

    async def call(self, args: object, ctx: CommandContext) -> LocalResult:
        _ = (args, ctx)
        return Skip()


@runtime_checkable
class WidgetDone(Protocol):
    """Callback a ``WidgetCommand`` invokes when its interaction completes.

    Encodes the exact keyword surface every stream agrees on::

        on_done(result, *, display, should_query, meta_messages,
                next_input, submit_next_input)
    """

    def __call__(
        self,
        result: object,
        *,
        display: object = None,
        should_query: bool = False,
        meta_messages: list | None = None,
        next_input: str | None = None,
        submit_next_input: bool = False,
    ) -> None: ...


@dataclass
class WidgetCommand:
    """A TUI-only interactive command driven by an ``on_done`` callback.

    ``on_done`` is invoked as::

        on_done(result, *, display, should_query, meta_messages,
                next_input, submit_next_input)
    """

    name: str
    surface: CommandSurface

    async def call(
        self, on_done: WidgetDone, ctx: CommandContext, args: object
    ) -> object:
        _ = (on_done, ctx, args)
        return None


Command = Union[PromptCommand, LocalCommand, WidgetCommand]


@runtime_checkable
class CommandExecutor(Protocol):
    """Executes a looked-up command for an interactive surface.

    NOT a second engine loop. ``prompt``-kind commands re-enter the surface's
    existing turn loop (``ctx.app.start_turn``); ``local``-kind run their
    callback and apply the ``LocalResult`` (Text -> commit, Compact -> request
    compaction, Skip -> nothing); ``widget``-kind open a dialog/palette. The
    single-turn-loop invariant is preserved — the executor NEVER drives
    ``run_turn_stream``.
    """

    async def run(self, command: Command, args: str, ctx: CommandContext) -> None: ...


@runtime_checkable
class CommandRegistry(Protocol):
    def lookup(self, name: str) -> Command | None: ...
    def list_for(self, surface: CommandSurface) -> list[Command]: ...
