"""Permission rules engine + gate skeleton for the Magi headless CLI.

This module is the PR-C1 foundation of the CLI permission gate. It is purely
additive: it changes no existing behavior and is wired in by later PRs. It
imports ONLY from the light, import-safe seams
(``magi_agent.cli.contracts`` and
``magi_agent.runtime.control``) so importing it never pulls in
``textual`` / ``google-adk``.

Pieces
------
``RulesEngine``
    Evaluates a :class:`ControlRequest` against static default rules plus
    runtime-remembered rules and returns a :data:`RuleVerdict`.

``RulesPermissionGate``
    A :class:`PermissionGate` that short-circuits on ``allow`` / ``deny`` from
    the rules engine, and on ``ask`` runs a resolve-once prompt-sink race (PR-C2:
    first sink to answer wins, losers are torn down, lifecycle mirrored into a
    :class:`ControlRequestStore`). Sink decisions carrying ``updates`` are
    persisted back into the rules engine so the next identical request
    short-circuits.

``HeadlessSink``
    A :class:`PromptSink` for the headless NDJSON CLI. On ``ask`` it emits
    exactly one ``control_request`` frame through the NDJSON writer and awaits
    the matching ``control_response`` (correlated by ``request_id``). Supports
    the ``default`` / ``acceptEdits`` / ``bypassPermissions`` permission modes
    (see :class:`HeadlessSink`).

ControlResponse schema parsed by HeadlessSink
---------------------------------------------
The inbound ``ControlResponse.response`` dict is parsed as::

    {
      "decision": "allow" | "deny",   # required; anything other than "allow"
                                      # is treated as a deny (fail-safe)
      "remember": bool,               # optional; when true with decision=allow,
                                      # produce a PermissionUpdate (remember-rule)
      "matcher": str,                 # optional; matcher for the remember-rule
                                      # (defaults to "*" when remember is true)
      "feedback": str | None,         # optional; carried on a deny (reject)
      "updated_input": {...} | None,  # optional; an allow with a rewritten input
      "interrupt": bool,              # optional; when true the deny interrupts
    }

Mapping:

- allow-once: ``{"decision": "allow"}`` -> ``PermissionDecision(kind="allow")``.
- allow+remember: ``{"decision": "allow", "remember": true, "matcher": "cmd=ls"}``
  -> ``PermissionDecision(kind="allow", updates=[PermissionUpdate(tool, matcher,
  "allow")])``.
- reject+feedback: ``{"decision": "deny", "feedback": "no"}`` ->
  ``PermissionDecision(kind="deny", feedback="no")``.
- updated_input: ``{"decision": "allow", "updated_input": {...}}`` ->
  ``PermissionDecision(kind="allow", updated_input={...})``.
- interrupt: ``{"decision": "deny", "interrupt": true}`` ->
  ``PermissionDecision(kind="deny", interrupt=True)``.

Matcher semantics
-----------------
A rule is the pair ``(tool, matcher)`` (see :class:`PermissionUpdate`). A rule
applies to a request only when ``rule.tool == req.tool_name`` (tool names match
exactly). The ``matcher`` then constrains *which* invocations of that tool the
rule covers, matched against a canonical string built from the request's
arguments — see :func:`canonical_request_key`:

- ``"*"`` (wildcard) matches ANY invocation of the tool. Specificity 0.
- any other matcher is an fnmatch-style **glob** matched against the canonical
  argument key (case-sensitively, deterministically). Specificity 1 — a glob
  match is always more specific than the catch-all ``"*"``.

Precedence (most-specific wins; deny breaks ties — fail-safe):

1. Among all applicable rules, higher specificity wins (a non-``"*"`` glob
   beats ``"*"``).
2. On EQUAL specificity, ``deny`` beats ``allow`` beats ``ask`` (deny is the
   safe choice when rules conflict at the same level).
3. If NO rule applies, the verdict is ``"ask"`` — we NEVER silently allow.

Remembered (runtime) rules and static (construction-time) rules share the same
matching/precedence logic; they are merely two sources concatenated, so a
specific remembered ``deny`` will still beat a static ``"*"`` ``allow``.
"""

from __future__ import annotations

import asyncio
import fnmatch
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from magi_agent.cli.contracts import (
    ControlRequest,
    PermissionDecision,
    PermissionGate,
    PermissionUpdate,
    PromptSink,
    RuleVerdict,
)
from magi_agent.cli.protocol import ControlRequestFrame, ControlResponse
from magi_agent.runtime.control import ControlRequestStore

if TYPE_CHECKING:  # pragma: no cover - typing only
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier


# Default location for the durable control-request JSONL log when the gate is
# ON but ``MAGI_CONTROL_STORE_PATH`` is unset.
_DEFAULT_DURABLE_STORE_RELPATH = ".magi/control/requests.jsonl"


def _default_control_store() -> ControlRequestStore:
    """Build the default ControlRequestStore honouring the durable gate (A7).

    Default (gate OFF) returns the volatile in-memory store, byte-identical to
    the prior ``ControlRequestStore()`` default. When ``MAGI_CONTROL_STORE_DURABLE``
    is truthy, returns a JSONL-backed durable store so pending approvals survive
    a process restart. Imports are lazy to preserve this module's light import
    contract (it must not pull config/env at import time).
    """
    from magi_agent.config.env import (
        control_store_durable_enabled,
        control_store_durable_path,
    )

    if not control_store_durable_enabled():
        return ControlRequestStore()

    from pathlib import Path

    from magi_agent.runtime.durable_control_store import DurableControlRequestStore

    path = control_store_durable_path()
    if path is None:
        path = Path.home() / _DEFAULT_DURABLE_STORE_RELPATH
    return DurableControlRequestStore(path=path)


__all__ = [
    "canonical_request_key",
    "RulesEngine",
    "RulesPermissionGate",
    "FrameWriter",
    "HeadlessSink",
    "PermissionMode",
    "EDIT_CLASS_TOOLS",
]

PermissionMode = Literal[
    "default",
    "acceptEdits",
    "bypassPermissions",
    "smartApprove",
]

# Tools auto-allowed in ``acceptEdits`` mode (no frame emitted). These are the
# file-mutating tools whose individual approvals are noise once the operator has
# opted into "accept edits". Matched exactly against ``req.tool_name``.
EDIT_CLASS_TOOLS: frozenset[str] = frozenset(
    {"FileEdit", "FileWrite", "Edit", "Write", "ApplyPatch"}
)


@runtime_checkable
class FrameWriter(Protocol):
    """The minimal NDJSON writer surface :class:`HeadlessSink` emits through.

    Satisfied by :class:`magi_agent.cli.ndjson.NdjsonWriter` and by any
    test fake exposing an async ``write(frame)``.
    """

    async def write(self, frame: object) -> None: ...


# ---------------------------------------------------------------------------
# Canonical request key
# ---------------------------------------------------------------------------
def canonical_request_key(req: ControlRequest) -> str:
    """Build a deterministic canonical string for a request's arguments.

    The key is what a non-wildcard matcher glob is matched against. It is a
    stable, sorted ``k=v`` join of the request arguments so the same logical
    invocation always produces the same key (dict ordering is irrelevant).

    Example: ``Bash(arguments={"cmd": "ls -la"})`` -> ``"cmd=ls -la"``.
    """
    items = sorted(req.arguments.items(), key=lambda kv: kv[0])
    return " ".join(f"{key}={value}" for key, value in items)


# Verdict ordering used for the deny-beats-allow-beats-ask tie-break.
_VERDICT_RANK: dict[str, int] = {"deny": 2, "allow": 1, "ask": 0}


def _coerce_verdict(decision: str) -> RuleVerdict:
    """Map a stored decision string onto a known verdict (default ``ask``)."""
    if decision in ("allow", "deny", "ask"):
        return decision  # type: ignore[return-value]
    return "ask"


class RulesEngine:
    """Deterministic tool-permission rules engine.

    Construct with optional ``default_rules`` (static rules). Add remembered
    rules at runtime via :meth:`add_rule` / :meth:`add_rules`. See the module
    docstring for matcher semantics and precedence.
    """

    def __init__(self, default_rules: list[PermissionUpdate] | None = None) -> None:
        self._static_rules: list[PermissionUpdate] = list(default_rules or [])
        self._remembered_rules: list[PermissionUpdate] = []

    def add_rule(self, update: PermissionUpdate) -> None:
        """Persist a single remembered rule for subsequent evaluations."""
        self._remembered_rules.append(update)

    def add_rules(self, updates: list[PermissionUpdate]) -> None:
        """Persist multiple remembered rules for subsequent evaluations."""
        for update in updates:
            self.add_rule(update)

    def _applicable(
        self, req: ControlRequest, key: str
    ) -> list[tuple[int, RuleVerdict]]:
        """Return ``(specificity, verdict)`` for every rule applying to ``req``."""
        applicable: list[tuple[int, RuleVerdict]] = []
        for rule in (*self._static_rules, *self._remembered_rules):
            if rule.tool != req.tool_name:
                continue
            verdict = _coerce_verdict(rule.decision)
            if rule.matcher == "*":
                applicable.append((0, verdict))
            elif fnmatch.fnmatchcase(key, rule.matcher):
                applicable.append((1, verdict))
        return applicable

    def evaluate(self, req: ControlRequest) -> RuleVerdict:
        """Resolve the verdict for ``req`` (``allow`` / ``deny`` / ``ask``).

        Default (no applicable rule) is the SAFE ``"ask"`` — never a silent
        allow.
        """
        key = canonical_request_key(req)
        applicable = self._applicable(req, key)
        if not applicable:
            return "ask"
        # Highest specificity first; among equals, highest verdict rank
        # (deny > allow > ask) wins the tie-break.
        best_specificity = max(spec for spec, _ in applicable)
        winners = [v for spec, v in applicable if spec == best_specificity]
        return max(winners, key=lambda v: _VERDICT_RANK[v])


class RulesPermissionGate(PermissionGate):
    """Permission gate backed by a :class:`RulesEngine` and prompt sink(s).

    ``allow`` / ``deny`` verdicts short-circuit immediately (no sink, no UI,
    no control frame). ``ask`` runs the prompt-sink race. When a sink's
    decision carries ``updates``, they are persisted into the rules engine so
    the next identical :meth:`check` short-circuits without touching a sink.
    """

    def __init__(
        self,
        *,
        rules: RulesEngine | None = None,
        sinks: list[PromptSink] | None = None,
        store: ControlRequestStore | None = None,
        smart_approve: "ReadOnlyClassifier | None" = None,
    ) -> None:
        self.rules: RulesEngine = rules if rules is not None else RulesEngine()
        self.sinks: list[PromptSink] = list(sinks) if sinks is not None else []
        # ``store`` is held for PR-C2 (the resolve-once race uses it); PR-C1
        # does not exercise it but accepts it so wiring stays stable. When no
        # store is injected, the backend is chosen by the durable gate (A7):
        # OFF (default) -> volatile in-memory store, byte-identical to before;
        # ON -> JSONL-backed durable store so pending approvals survive a
        # restart. An explicitly injected ``store`` always wins.
        self.store: ControlRequestStore = (
            store if store is not None else _default_control_store()
        )
        # SmartApprove classifier (PR3). ``None`` means disabled (DEFAULT).
        # When set, a rule-miss ``ask`` is offered to the classifier BEFORE the
        # sink race — it can recover a read-only call as ``allow``.
        # NEVER consulted on an explicit ``deny`` (the ``deny`` early-return
        # happens first, so the classifier is structurally unreachable for deny).
        self._smart_approve: "ReadOnlyClassifier | None" = smart_approve

    async def check(self, req: ControlRequest) -> PermissionDecision:
        verdict = self.rules.evaluate(req)
        if verdict == "allow":
            return PermissionDecision(kind="allow")
        if verdict == "deny":
            # Explicit deny is NEVER auto-recovered — classifier not consulted.
            return PermissionDecision(kind="deny")

        # verdict == "ask" (rule miss)
        # SmartApprove: if a classifier is wired, ask it BEFORE the sink race.
        # A True return means the tool is read-only → allow without prompting.
        # A False return (or no classifier) falls through to the normal race.
        if self._smart_approve is not None:
            try:
                is_readonly = await self._smart_approve.classify(req)
            except Exception:  # noqa: BLE001 — fail closed
                is_readonly = False
            if is_readonly:
                return PermissionDecision(kind="allow")

        decision = await self._race(req)

        # Persist any remembered rules so the NEXT identical check short-circuits.
        if decision.updates:
            self.rules.add_rules(decision.updates)
        return decision

    async def _race(
        self,
        req: ControlRequest,
        sinks: list[PromptSink] | None = None,
    ) -> PermissionDecision:
        """Resolve an ``ask`` request via a resolve-once prompt-sink race.

        Behavior:
        - No sinks -> safe ``deny`` (no interactive surface to answer).
        - One or more sinks -> launch ``sink.ask(req)`` for EACH sink
          concurrently. The FIRST task to answer *claims* the resolution and
          becomes authoritative; every other task is cancelled and any late
          answer it produced is dropped (resolve-once). A single sink still
          flows through the same machinery (no incorrect special-casing).

        Lifecycle mirroring: a pending :class:`ControlRequestStore` entry is
        created before the race; on a win it is ``resolve_request``-d, and on
        teardown (no winner, or to mark the request terminal) it is
        ``cancel_request``-d. The store is in-memory and disabled-by-default in
        the runtime, so we drive it directly as the CLI-side consumer. Store
        bookkeeping never alters the returned decision (best-effort).

        No asyncio tasks are leaked: every created task is awaited or cancelled
        (and its ``CancelledError`` suppressed) in a ``finally`` block.
        """
        active_sinks = list(sinks) if sinks is not None else self.sinks
        if not active_sinks:
            return PermissionDecision(kind="deny")

        store_request_id = self._store_create(req)

        # resolve-once claim primitive: only the first finisher may set the
        # winning decision. A boolean flag is sufficient because the race runs
        # on a single event loop (no preemption between the flag check and set
        # within a synchronous block).
        claimed = False
        winner: PermissionDecision | None = None

        tasks: list[asyncio.Task[PermissionDecision]] = [
            asyncio.ensure_future(sink.ask(req)) for sink in active_sinks
        ]
        try:
            pending = set(tasks)
            while pending and winner is None:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    try:
                        decision = task.result()
                    except asyncio.CancelledError:
                        continue
                    except Exception:  # noqa: BLE001
                        # A sink that errored cannot claim the resolution; the
                        # race continues with the remaining sinks (and falls
                        # back to a safe deny if all sinks fail).
                        continue
                    if not claimed:
                        claimed = True
                        winner = decision
                        break
                    # claimed already: this is a redundant answer -> drop it.
            if winner is not None:
                self._store_resolve(store_request_id, winner)
                return winner
            # No sink produced a decision (all cancelled/errored) -> safe deny.
            self._store_cancel(store_request_id, "no_sink_decision")
            return PermissionDecision(kind="deny")
        finally:
            # Tear down EVERY task: cancel the losers / stragglers, then await
            # each so no task is leaked and any late answer is discarded.
            for task in tasks:
                if not task.done():
                    task.cancel()
            for task in tasks:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:  # noqa: BLE001
                    pass

    # -- ControlRequestStore lifecycle mirroring (best-effort) --------------
    def _store_create(self, req: ControlRequest) -> str | None:
        """Create a pending store record; return its request_id (or None)."""
        try:
            result = self.store.create_tool_permission_request(
                session_key="cli",
                turn_id=req.turn_id or None,
                channel_name=None,
                source="turn",
                prompt=req.reason or req.tool_name,
                proposed_input=dict(req.arguments),
                idempotency_key=req.request_id or f"{req.tool_name}:{req.turn_id}",
                now=time.time(),
                timeout_ms=0,
            )
            return result.record.request_id
        except Exception:  # noqa: BLE001
            return None

    def _store_resolve(
        self, store_request_id: str | None, decision: PermissionDecision
    ) -> None:
        if store_request_id is None:
            return
        store_decision = "approved" if decision.kind == "allow" else "denied"
        try:
            self.store.resolve_request(
                store_request_id,
                decision=store_decision,  # type: ignore[arg-type]
                now=time.time(),
                feedback=decision.feedback,
                updated_input=decision.updated_input,
            )
        except Exception:  # noqa: BLE001
            pass

    def _store_cancel(self, store_request_id: str | None, reason: str) -> None:
        if store_request_id is None:
            return
        try:
            self.store.cancel_request(
                store_request_id, reason=reason, now=time.time()
            )
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# HeadlessSink
# ---------------------------------------------------------------------------
class HeadlessSink(PromptSink):
    """NDJSON prompt sink for the headless CLI.

    On :meth:`ask` (in ``default`` mode) it emits exactly ONE
    ``control_request`` frame through the injected NDJSON ``writer`` and then
    awaits the matching ``control_response``. Correlation is by ``request_id``.

    Response delivery mechanism
    ---------------------------
    There is no stdin reader yet (deferred to a later stream). Instead the sink
    exposes :meth:`deliver`: whatever component owns the inbound NDJSON stream
    (a future stdin reader — or a test) parses a :class:`ControlResponse` and
    calls ``sink.deliver(response)``. The sink keeps a per-``request_id``
    :class:`asyncio.Future` registry; ``deliver`` resolves the matching future,
    which wakes the awaiting :meth:`ask`. This keeps the sink fully testable
    without any I/O loop.

    Permission modes
    ----------------
    - ``bypassPermissions`` — blanket allow, NO frame emitted (the only
      auto-allow-everything path; clearly opt-in).
    - ``acceptEdits`` — auto-allow edit-class tools (:data:`EDIT_CLASS_TOOLS`)
      with NO frame; every other tool still prompts.
    - ``default`` — always PROMPT (emit a frame + await a response). Never a
      silent auto-allow.

    Bounded dedup
    -------------
    ``_resolved`` is a bounded ordered set of request_ids that have already been
    answered or cancelled. Late/duplicate ``control_response`` frames for such
    an id are ignored. The set is capped at :data:`_DEDUP_CAP` (oldest evicted)
    so it cannot grow unbounded.
    """

    _DEDUP_CAP = 1024

    def __init__(
        self,
        writer: FrameWriter,
        *,
        permission_mode: PermissionMode = "default",
        can_prompt: bool = True,
    ) -> None:
        self._writer = writer
        self.permission_mode: PermissionMode = permission_mode
        self._can_prompt = can_prompt
        self._pending: dict[str, asyncio.Future[ControlResponse]] = {}
        # Bounded "already terminal" set (request_id -> None), insertion-ordered
        # so the oldest entries are evicted first when the cap is exceeded.
        self._resolved: OrderedDict[str, None] = OrderedDict()
        # Set once the inbound channel is closed (EOF): subsequent asks deny
        # immediately rather than registering a future that can never resolve.
        self._closed = False
        # Responses that arrived BEFORE their ask registered (e.g. a pre-loaded
        # inbound buffer in tests, or a fast host). Stashed by request_id and
        # consumed by the matching ``ask``. Bounded by the same dedup cap.
        self._early: OrderedDict[str, ControlResponse] = OrderedDict()

    # -- public delivery surface -------------------------------------------
    def deliver(self, response: ControlResponse) -> None:
        """Deliver an inbound ``control_response`` to its awaiting :meth:`ask`.

        Idempotent for already-resolved/cancelled ids (bounded dedup): a late or
        duplicate response is silently ignored. Safe to call from the inbound
        reader / tests; never raises on an unknown or stale id.
        """
        request_id = response.request_id
        if request_id in self._resolved:
            return  # late/duplicate -> drop
        future = self._pending.get(request_id)
        if future is None:
            # Response arrived before the matching ``ask`` registered. Stash it
            # so ``ask`` can consume it immediately on registration (handles a
            # pre-loaded inbound buffer / a fast host). Do NOT mark resolved yet
            # — the ask must still see it.
            self._early[request_id] = response
            self._early.move_to_end(request_id)
            while len(self._early) > self._DEDUP_CAP:
                self._early.popitem(last=False)
            return
        if future.done():
            return  # already settled -> drop
        self._mark_resolved(request_id)
        future.set_result(response)

    def _mark_resolved(self, request_id: str) -> None:
        self._resolved[request_id] = None
        self._resolved.move_to_end(request_id)
        while len(self._resolved) > self._DEDUP_CAP:
            self._resolved.popitem(last=False)

    def close(self) -> None:
        """Fail-close every still-pending ``ask``.

        Called when the inbound channel reaches EOF (no answer will ever come):
        each awaiting :meth:`ask` is woken with a ``deny`` ``control_response`` so
        the gate's race resolves to a safe deny instead of hanging forever. We
        never auto-allow on EOF.
        """

        self._closed = True
        for request_id, future in list(self._pending.items()):
            if future.done():
                continue
            self._mark_resolved(request_id)
            future.set_result(
                ControlResponse(
                    request_id=request_id,
                    response={"decision": "deny"},
                )
            )

    # -- PromptSink ---------------------------------------------------------
    async def ask(self, req: ControlRequest) -> PermissionDecision:
        if self.permission_mode == "bypassPermissions":
            # Blanket allow: the ONLY no-prompt allow-everything path. No frame.
            return PermissionDecision(kind="allow")
        request_id = req.request_id
        # Was a response delivered BEFORE this ask registered (e.g. a pre-loaded
        # inbound buffer / a fast host)? That answer is real, so honor it even if
        # the channel has since closed (EOF only means no MORE answers).
        has_early = request_id in self._early
        if (
            self.permission_mode == "acceptEdits"
            and req.tool_name in EDIT_CLASS_TOOLS
            and not has_early
        ):
            # Auto-allow edit-class tools without a frame; non-edit tools fall
            # through to the prompt/deny path below.
            return PermissionDecision(kind="allow")
        if self._closed and not has_early:
            # Inbound channel closed (EOF) and no stashed answer: no response can
            # arrive, so do not emit a frame / register a future — fail-safe deny.
            return PermissionDecision(kind="deny")
        if not self._can_prompt and not has_early:
            # No inbound approver is available. acceptEdits handled edit tools
            # above; every remaining ask fails closed instead of hanging forever.
            return PermissionDecision(kind="deny")

        # default (or acceptEdits + non-edit tool): emit exactly ONE frame and
        # await the matching response.
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ControlResponse] = loop.create_future()
        self._pending[request_id] = future

        # Pre-resolve from the early stash if present; we STILL emit the
        # control_request frame below (so the wire protocol is observable) — the
        # subsequent ``await future`` then returns immediately.
        early = self._early.pop(request_id, None)
        if early is not None and not future.done():
            future.set_result(early)

        frame = ControlRequestFrame(
            request_id=request_id,
            request={
                "tool_name": req.tool_name,
                "arguments": dict(req.arguments),
                "reason": req.reason,
            },
        )
        try:
            await self._writer.write(frame)
            response = await future
            self._mark_resolved(request_id)
            return self._translate(req, response)
        except asyncio.CancelledError:
            # Loser teardown (or interrupt): stop awaiting, mark terminal so a
            # late deliver() is dropped, and re-raise so the race can cancel us.
            self._mark_resolved(request_id)
            raise
        finally:
            self._pending.pop(request_id, None)

    # -- response translation ----------------------------------------------
    def _translate(
        self, req: ControlRequest, response: ControlResponse
    ) -> PermissionDecision:
        """Translate a ``ControlResponse.response`` dict into a decision.

        See the module docstring for the parsed schema. Anything that is not an
        explicit ``"allow"`` is treated as a deny (fail-safe).
        """
        body = response.response or {}
        decision = body.get("decision")
        if decision == "allow":
            updates: list[PermissionUpdate] = []
            if bool(body.get("remember")):
                matcher = body.get("matcher")
                updates.append(
                    PermissionUpdate(
                        tool=req.tool_name,
                        matcher=str(matcher) if matcher else "*",
                        decision="allow",
                    )
                )
            updated_input = body.get("updated_input")
            return PermissionDecision(
                kind="allow",
                updates=updates,
                updated_input=updated_input
                if isinstance(updated_input, dict)
                else None,
            )
        # deny (or any non-allow): reject, optionally with feedback / interrupt.
        feedback = body.get("feedback")
        updates_deny: list[PermissionUpdate] = []
        if bool(body.get("remember")):
            matcher = body.get("matcher")
            updates_deny.append(
                PermissionUpdate(
                    tool=req.tool_name,
                    matcher=str(matcher) if matcher else "*",
                    decision="deny",
                )
            )
        return PermissionDecision(
            kind="deny",
            updates=updates_deny,
            feedback=str(feedback) if feedback is not None else None,
            interrupt=bool(body.get("interrupt")),
        )
