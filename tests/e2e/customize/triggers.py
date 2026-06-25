"""Per-slot synthetic trigger drivers for the F-QA matrix harness.

Each trigger function exercises the runtime chokepoint that fires the
specified lifecycle slot using the smallest possible setup. F-QA1 covers
three slots:

* :func:`trigger_pre_final` — pre-final gate. Routes by kind:
  - ``deterministic_ref`` / shacl_constraint via the gate compile seam
    (``magi_agent.cli.real_runner._apply_customize_verification`` for
    deterministic_ref; the shacl verifier kernel directly for
    shacl_constraint).
  - ``llm_criterion`` via ``MagiEngineDriver._maybe_llm_criterion_block``
    with a patched judge that returns the verdict the matrix expects.
  - ``shell_command`` / ``shell_check`` via the lifecycle_audit fan-out
    helpers ``run_shell_command_at_pre_final`` /
    ``run_shell_check_at_pre_final``.

* :func:`trigger_before_tool_use` — wrap
  :func:`magi_agent.facades.execute_tool_with_hooks` with a stub
  :class:`ToolDispatcher` and :class:`HookBus`. The facade's
  before-tool branch invokes the prompt_injection applier, the
  tool_perm matcher (consulted by the production permission layer
  separately — we drive it directly for tool_perm rules), and the
  shell_command/shell_check before-tool helpers.

* :func:`trigger_after_tool_use` — same facade rig, asserts on
  mutations (output_rewrite) / overrides (llm_criterion after-tool gate)
  applied to the returned :class:`ToolResult`.

The trigger functions return a :class:`TriggerOutcome` the asserter
inspects. They never raise on a rule failing to fire — the *asserter*
is responsible for translating "did the rule fire as expected?" into a
test pass/fail.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from magi_agent.harness.resolved import build_default_resolved_harness_state
from magi_agent.hooks.bus import HookBus, HookBusObservation, HookBusRunResult
from magi_agent.hooks.context import HookContext
from magi_agent.tools.context import ToolContext
from magi_agent.tools.dispatcher import ToolDispatcher
from magi_agent.tools.result import ToolResult


# ---------------------------------------------------------------------------
# Outcome dataclass
# ---------------------------------------------------------------------------


@dataclass
class TriggerOutcome:
    """Captured runtime side effects from one synthetic trigger."""

    # Audit records the lifecycle_audit fan-out emitted (may be empty when
    # the slot's runtime fan-out is in-band — e.g. tool_perm returns a
    # (decision, rule_id) pair, not an audit ledger record).
    audit_records: list[dict[str, Any]] = field(default_factory=list)
    # ``proceed`` / ``block`` / ``ask`` / ``override`` / ``error``. The
    # asserter maps the matrix-declared action onto this verdict.
    runtime_verdict: str = "proceed"
    # Arbitrary per-trigger side-effect bag (the asserter inspects this for
    # kind-specific evidence, e.g. ``tool_result`` for after_tool_use,
    # ``required_validators`` for deterministic_ref, ``decision`` for
    # tool_perm). Keeping the bag open avoids one TriggerOutcome subclass
    # per (kind, slot) combo.
    side_effects: dict[str, Any] = field(default_factory=dict)


def _continue_hookbus() -> HookBus:
    """Build a ``HookBus`` mock that returns ``continue`` for every point.

    The facade's before/after hooks only consult ``HookBus.run`` for
    BEFORE_TOOL_USE / AFTER_TOOL_USE points; everything else is bypassed.
    """
    bus = MagicMock(spec=HookBus)
    bus.run = MagicMock(
        return_value=HookBusRunResult(
            final_action="continue",
            results=(),
            observation=HookBusObservation(),
            harness_state=build_default_resolved_harness_state(),
        )
    )
    return bus


def _stub_dispatcher(
    *, status: str = "ok", output: str = "PASS"
) -> AsyncMock:
    """Return an ``AsyncMock(spec=ToolDispatcher)`` with a deterministic dispatch.

    Default ``output="PASS"`` so an ``llm_criterion`` rule with a binary
    criterion can pass in the after-tool path without needing a real LLM
    call (the asserter monkeypatches the judge separately when ``override``
    is the expected action).
    """
    dispatcher = AsyncMock(spec=ToolDispatcher)
    dispatcher.dispatch = AsyncMock(
        return_value=ToolResult(status=status, output=output)
    )
    return dispatcher


# ---------------------------------------------------------------------------
# pre_final
# ---------------------------------------------------------------------------


async def trigger_pre_final(
    *,
    kind: str,
    rule_id: str,
    expected_action: str,
    judge_factory: Callable[[], Callable[..., Awaitable[tuple[bool, str]]]]
    | None = None,
) -> TriggerOutcome:
    """Drive whatever pre-final chokepoint the rule's kind fans out at.

    The branches mirror the production runtime entry points; they do not
    invent any new wire. ``judge_factory`` is only consulted for
    ``llm_criterion`` rules — the conftest fixture monkeypatches the
    criterion engine so we never need a live LLM call.
    """
    if kind == "deterministic_ref":
        return _trigger_pre_final_deterministic_ref()
    if kind == "shacl_constraint":
        return _trigger_pre_final_shacl_constraint(expected_action=expected_action)
    if kind == "llm_criterion":
        return await _trigger_pre_final_llm_criterion(
            rule_id=rule_id,
            expected_action=expected_action,
            judge_factory=judge_factory,
        )
    if kind in {"shell_command", "shell_check"}:
        return await _trigger_pre_final_shell(kind=kind)
    raise ValueError(f"trigger_pre_final has no branch for kind={kind!r}")


def _trigger_pre_final_deterministic_ref() -> TriggerOutcome:
    """Drive ``_apply_customize_verification`` with a seed validator list.

    Firing semantics: the rule's ``ref`` is injected into the returned
    list when both master flags are ON (and the producer registry knows
    the ref). Asserter compares ``required_validators`` against the
    seed list to detect the injection.
    """
    from magi_agent.cli.real_runner import _apply_customize_verification

    seed = ["seed:ref"]
    out = _apply_customize_verification(list(seed))
    return TriggerOutcome(
        side_effects={
            "required_validators": list(out),
            "seed_validators": list(seed),
        },
    )


def _trigger_pre_final_shacl_constraint(
    *, expected_action: str
) -> TriggerOutcome:
    """Run the SHACL kernel against a synthetic TestRun record.

    For ``expected_action == "block"`` we feed a non-conforming record
    (exit_code=1) so the verifier reports a violation. The action label
    on the rule is honored by the wrapping runtime gate, not by
    ``run_shacl_rule`` itself — this function captures the verifier's
    raw verdict so the asserter can mirror the "block-on-violation"
    contract.
    """
    try:
        from magi_agent.evidence.shacl_verifier import run_shacl_rule
        from magi_agent.evidence.types import EvidenceRecord, EvidenceSource
    except ImportError:
        # Optional deps (rdflib + pyshacl) absent on minimal envs — surface
        # via verdict so the conftest skip path can fire.
        return TriggerOutcome(
            runtime_verdict="error",
            side_effects={"reason": "shacl_deps_missing"},
        )

    from tests.e2e.customize.payload_factory import _SHACL_TEST_RUN_EXIT_ZERO

    exit_code = 1 if expected_action == "block" else 0
    record = EvidenceRecord(
        type="TestRun",
        status="ok",
        observedAt=1_718_000_000,
        source=EvidenceSource(kind="verifier"),
        fields={"exitCode": exit_code},
    )
    result = run_shacl_rule(
        [record],
        _SHACL_TEST_RUN_EXIT_ZERO,
        "fqa1-shacl",
        observed_at=1_718_000_000,
    )
    verdict = "block" if result.status == "failed" else "proceed"
    return TriggerOutcome(
        runtime_verdict=verdict,
        side_effects={"shacl_status": result.status},
    )


async def _trigger_pre_final_llm_criterion(
    *,
    rule_id: str,
    expected_action: str,
    judge_factory: Callable[[], Callable[..., Awaitable[tuple[bool, str]]]]
    | None,
) -> TriggerOutcome:
    """Drive ``MagiEngineDriver._maybe_llm_criterion_block`` with a patched judge.

    The asserter's harness installs the patched judge before calling us.
    We simply drive the engine seam and capture the returned block-reason
    (``None`` ⇒ no block) so the asserter can map matrix-action onto
    "expected verdict".

    Drives via the same code path
    ``tests/customize_firing/test_llm_criterion_firing.py`` exercises
    so the trigger never invents a new wire.
    """
    _ = (rule_id, expected_action, judge_factory)  # asserter installed the patch
    from magi_agent.cli.engine import MagiEngineDriver

    driver = MagiEngineDriver(criterion_model_factory=lambda: object())
    reason = await driver._maybe_llm_criterion_block(
        final_text="The market grew 40% last year according to internal estimates."
    )
    verdict = "block" if reason else "proceed"
    return TriggerOutcome(
        runtime_verdict=verdict,
        side_effects={"reason": reason},
    )


async def _trigger_pre_final_shell(*, kind: str) -> TriggerOutcome:
    """Drive ``run_shell_*_at_pre_final`` fan-out.

    Returns the audit records the runtime would record. The matrix is
    audit-shaped (a ``failed`` shell_check at action=block returns
    verdict ``block``; otherwise the helper returns ``proceed``).
    """
    if kind == "shell_command":
        from magi_agent.customize.lifecycle_audit import (
            run_shell_command_at_pre_final,
        )

        audits, _verdict = await run_shell_command_at_pre_final(draft_text="x")
        # shell_command fan-out does not surface a runtime verdict — block
        # at this slot is honored by the governed_turn caller, not the
        # helper. We surface "block" if any audit record's exit_code != 0.
        verdict = "proceed"
        for record in audits:
            if (
                record.get("status") == "executed"
                and record.get("exit_code", 0) != 0
            ):
                verdict = "block"
                break
        return TriggerOutcome(
            audit_records=list(audits),
            runtime_verdict=verdict,
        )

    # shell_check
    from magi_agent.customize.lifecycle_audit import (
        run_shell_check_at_pre_final,
    )

    audits, verdict = await run_shell_check_at_pre_final(draft_text="x")
    return TriggerOutcome(
        audit_records=list(audits),
        runtime_verdict=verdict,
    )


# ---------------------------------------------------------------------------
# before_tool_use
# ---------------------------------------------------------------------------


async def trigger_before_tool_use(
    *,
    kind: str,
    rule_id: str,
    expected_action: str,
    tool_name: str = "shell_exec",
    arguments: dict[str, Any] | None = None,
) -> TriggerOutcome:
    """Drive the before-tool chokepoint for the rule's kind.

    Routing:
      * ``tool_perm`` → ``customize.tool_perm.matched_decision`` (the
        seam the production permission layer consults pre-dispatch).
      * ``prompt_injection`` → ``execute_tool_with_hooks`` with a stub
        dispatcher; the asserter inspects the dispatcher's recorded
        arguments to detect the mutation.
      * ``shell_command`` / ``shell_check`` → ``execute_tool_with_hooks``
        which calls ``run_shell_*_at_before_tool_use`` internally; the
        asserter inspects the returned ``ToolResult.status`` for
        ``"blocked"`` when action == ``"block"``.
    """
    _ = expected_action  # asserter consumes this; we always drive deterministically
    if arguments is None:
        arguments = {"command": "ls"}

    if kind == "tool_perm":
        return _trigger_before_tool_use_tool_perm(
            tool_name=tool_name, arguments=arguments
        )

    if kind in {"prompt_injection", "shell_command", "shell_check"}:
        return await _trigger_before_tool_use_via_facade(
            tool_name=tool_name,
            arguments=arguments,
        )

    raise ValueError(
        f"trigger_before_tool_use has no branch for kind={kind!r}"
    )


def _trigger_before_tool_use_tool_perm(
    *, tool_name: str, arguments: dict[str, Any]
) -> TriggerOutcome:
    """Call ``matched_decision`` directly — the seam runtime consults pre-dispatch."""
    from magi_agent.customize.tool_perm import matched_decision

    decision = matched_decision(
        tool_name=tool_name,
        arguments=arguments,
        current_scope="always",
    )
    if decision is None:
        return TriggerOutcome(
            runtime_verdict="proceed",
            side_effects={"decision": None},
        )
    verdict_label, rule_id = decision
    runtime_verdict = "block" if verdict_label == "deny" else "ask"
    return TriggerOutcome(
        runtime_verdict=runtime_verdict,
        side_effects={"decision": decision, "matched_rule_id": rule_id},
    )


async def _trigger_before_tool_use_via_facade(
    *,
    tool_name: str,
    arguments: dict[str, Any],
) -> TriggerOutcome:
    """Run a single tool dispatch through the facade and capture the result."""
    from magi_agent.facades import execute_tool_with_hooks

    dispatcher = _stub_dispatcher(status="ok", output="ok")
    bus = _continue_hookbus()

    result, before, after = await execute_tool_with_hooks(
        dispatcher,
        bus,
        tool_name=tool_name,
        arguments=arguments,
        context=ToolContext(botId="b"),
        hook_context=HookContext(botId="b"),
        harness_state=build_default_resolved_harness_state(),
        mode="act",
    )
    verdict = "block" if result.status == "blocked" else "proceed"
    return TriggerOutcome(
        runtime_verdict=verdict,
        side_effects={
            "tool_result": result,
            "dispatched_arguments": (
                dispatcher.dispatch.call_args.args[1]
                if dispatcher.dispatch.call_args is not None
                else None
            ),
            "before_hook_result": before,
            "after_hook_result": after,
        },
    )


# ---------------------------------------------------------------------------
# after_tool_use
# ---------------------------------------------------------------------------


async def trigger_after_tool_use(
    *,
    kind: str,
    rule_id: str,
    expected_action: str,
    tool_name: str = "shell_exec",
    dispatch_output: str = "hello AKIABCDEFGHIJKLMNOPQ world PASS",
) -> TriggerOutcome:
    """Drive the after-tool chokepoint for the rule's kind.

    The facade composes ``output_rewrite``'s redact, the after-tool
    HookBus replace branch, and (for ``llm_criterion`` override rules)
    the after-tool ingestion gate. The default dispatch output carries
    both an AWS-key-shaped substring (so ``output_rewrite`` has
    something to redact) and the literal ``"PASS"`` (so an
    ``llm_criterion`` criterion authored as "Does the output contain
    the literal PASS?" can pass without an LLM call).
    """
    _ = (rule_id, expected_action)  # asserter inspects side_effects

    if kind in {"output_rewrite", "shell_command", "shell_check"}:
        return await _trigger_after_tool_use_via_facade(
            tool_name=tool_name,
            dispatch_output=dispatch_output,
        )
    if kind == "llm_criterion":
        return await _trigger_after_tool_use_llm_criterion(
            tool_name=tool_name,
            dispatch_output=dispatch_output,
        )
    raise ValueError(f"trigger_after_tool_use has no branch for kind={kind!r}")


async def _trigger_after_tool_use_via_facade(
    *,
    tool_name: str,
    dispatch_output: str,
) -> TriggerOutcome:
    """Generic after-tool driver — the facade applies the rule mutators."""
    from magi_agent.facades import execute_tool_with_hooks

    dispatcher = _stub_dispatcher(status="ok", output=dispatch_output)
    bus = _continue_hookbus()

    result, before, after = await execute_tool_with_hooks(
        dispatcher,
        bus,
        tool_name=tool_name,
        arguments={"command": "ls"},
        context=ToolContext(botId="b"),
        hook_context=HookContext(botId="b"),
        harness_state=build_default_resolved_harness_state(),
        mode="act",
    )
    return TriggerOutcome(
        runtime_verdict="proceed",
        side_effects={
            "tool_result": result,
            "original_output": dispatch_output,
            "before_hook_result": before,
            "after_hook_result": after,
        },
    )


async def _trigger_after_tool_use_llm_criterion(
    *,
    tool_name: str,
    dispatch_output: str,
) -> TriggerOutcome:
    """Drive the after-tool ingestion gate (``CustomizeAfterToolControl``).

    The gate's ``apply_after_tool`` returns an override dict on a fail
    verdict / content-match hit and ``None`` otherwise. The conftest's
    ``judge_factory`` controls the verdict.
    """
    from magi_agent.customize.after_tool_gate import CustomizeAfterToolControl

    control = CustomizeAfterToolControl(model_factory=lambda: object())

    tool_stub = MagicMock()
    tool_stub.name = tool_name

    override = await control.apply_after_tool(
        ctx=None,
        tool=tool_stub,
        args={},
        tool_context=None,
        result=dispatch_output,
    )
    verdict = "override" if override is not None else "proceed"
    return TriggerOutcome(
        runtime_verdict=verdict,
        side_effects={"override": override, "original_output": dispatch_output},
    )


# ---------------------------------------------------------------------------
# Sync wrappers
# ---------------------------------------------------------------------------


def run_async(coro: Awaitable[TriggerOutcome]) -> TriggerOutcome:
    """Sync entry for pytest test functions that want to remain non-async."""
    return asyncio.run(coro)  # type: ignore[arg-type]
