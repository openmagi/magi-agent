"""Tests for the tool-not-found soft-fail plugin (PR-R).

Kevin 0.1.97 direct-debug: a SOTA-spawn child (Gemini 3.1 Pro) requested
``Bash`` which is not in the readonly child toolset. Google ADK's
``_get_tool`` raises ``ValueError("Tool 'Bash' not found.\\nAvailable tools:
Calculation, FileRead, ...")`` and the child TURN terminated with
``llm_call_exception`` because no ``on_tool_error_callback`` converted the
raise into a corrective tool_result.

Kevin's architectural note: "tool 한번 실패했다고 다른 방법을 안찾고 바로
포기하는거 자체가 이상한 거 아닌가." Claude Code / OpenAI Agents SDK /
OpenCode all soft-fail unknown-tool: the tool_use returns a "Tool 'X' does not
exist; available: [...]" tool_result and the model picks a valid tool from
that list on the next iteration.

Retry-policy shift (post 0.1.97)
--------------------------------
Kevin follow-up direction: "단순히 툴 실패시 n회 재시도 이런식의 하드룰보다는
모델에게 flexibility를 보장하는 근본적인 솔루션으로 하는게 좋을듯."

So the plugin no longer imposes a per-invocation "n retries then hard-fail"
cap by default. Every unknown-tool call surfaces as a corrective tool_result;
retry policy is delegated to the model + the runtime's existing turn-level
iteration cap. An operator may opt in to a numeric cap via
``MAGI_TOOL_NOT_FOUND_ATTEMPT_CAP=N`` (N >= 1); default ``0`` means unlimited.

Behavior contract exercised here:

* Only handles the ADK ``Tool '<name>' not found`` shape (matched on the
  placeholder tool ADK builds: ``description == "Tool not found"``, or on the
  ``ValueError`` message shape). Every other raise returns ``None`` so the
  generic tool-exception reflection plugin gets first crack.
* Default (unlimited): every unknown-tool call becomes a corrective dict
  with ``error_code="tool_not_found"`` and the available-tools list; the
  plugin never returns ``None`` for the ADK unknown-tool shape.
* Operator opt-in (``attempt_cap >= 1``): under budget returns the
  corrective dict; at/over budget returns ``None`` so ADK re-raises the
  original ``ValueError``. PR-3's containment then surfaces the terminal
  event with the distinct exhausted error_code.
* Flag ``MAGI_TOOL_NOT_FOUND_SOFT_FAIL`` is default-ON. Safe because the
  retry pool is bounded by the tools the runtime already advertises to the
  model, and the error text is already surfaced by ADK (no new public
  information).

Pure unit tests. No ADK Runner, no network, no model.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from magi_agent.adk_bridge.tool_not_found_soft_fail import (
    TOOL_NOT_FOUND_ERROR_CODE,
    TOOL_NOT_FOUND_RETRY_EXHAUSTED_CODE,
    TOOL_NOT_FOUND_SOFT_FAIL_RESPONSE_TYPE,
    MagiToolNotFoundSoftFailPlugin,
    build_tool_not_found_soft_fail_plugin,
)
from magi_agent.config.env import parse_tool_not_found_soft_fail_env


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #


class _AdkPlaceholderTool:
    """Mirrors what ADK builds when ``_get_tool`` raises: a BaseTool with the
    requested name and the canonical description marker."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = "Tool not found"


class _RealTool:
    """A tool that exists (used to model non-unknown-tool raises)."""

    def __init__(self, name: str, description: str = "does a thing") -> None:
        self.name = name
        self.description = description


class _FakeCtx:
    def __init__(self, invocation_id: str = "inv-test") -> None:
        self.invocation_id = invocation_id


def _adk_unknown_tool_error(requested: str, available: tuple[str, ...]) -> ValueError:
    """Build a ValueError with the exact text shape ADK
    ``google.adk.flows.llm_flows.functions._get_tool`` raises."""
    joined = ", ".join(available)
    body = (
        f"Tool '{requested}' not found.\nAvailable tools: {joined}\n\n"
        "Possible causes:\n  1. LLM hallucinated the function name - review "
        "agent instruction clarity\n"
    )
    return ValueError(body)


def _reflect(
    plugin: MagiToolNotFoundSoftFailPlugin,
    *,
    tool: Any,
    tool_context: Any,
    error: Exception,
) -> dict[str, Any] | None:
    return _run(
        plugin.on_tool_error_callback(
            tool=tool,
            tool_args={},
            tool_context=tool_context,
            error=error,
        )
    )


# --------------------------------------------------------------------------- #
# 1. Soft-fail: unknown tool returns a tool_result, does NOT raise             #
# --------------------------------------------------------------------------- #


def test_unknown_tool_returns_tool_result_not_raise() -> None:
    """Kevin's core fix: instead of ADK re-raising the ValueError, the plugin
    hands back a structured tool_result the model can consume."""
    plugin = MagiToolNotFoundSoftFailPlugin()

    result = _reflect(
        plugin,
        tool=_AdkPlaceholderTool("Bash"),
        tool_context=_FakeCtx("inv-unknown"),
        error=_adk_unknown_tool_error(
            "Bash", ("Calculation", "FileRead", "GitDiff", "Glob", "Grep")
        ),
    )

    assert result is not None, "unknown tool must NOT propagate the raise"
    assert result["response_type"] == TOOL_NOT_FOUND_SOFT_FAIL_RESPONSE_TYPE
    assert result["status"] == "error"
    assert result["error_code"] == TOOL_NOT_FOUND_ERROR_CODE
    assert result["requested_tool"] == "Bash"
    # The available-tools list mirrors ADK's own error text so the model can
    # pick a valid tool on the next iteration.
    assert result["available_tools"] == [
        "Calculation",
        "FileRead",
        "GitDiff",
        "Glob",
        "Grep",
    ]
    # Retry-attempt is still exposed for observability; the operator-imposed
    # cap key is only present when the operator explicitly opted in.
    assert result["retry_attempt"] == 1
    assert "attempt_cap" not in result, (
        "the default unlimited configuration must NOT advertise a numeric cap"
    )
    # The guidance text names the requested tool AND spells out the choices,
    # so the model has everything it needs on the very next iteration.
    message = result["error_message"]
    assert "Bash" in message
    assert "Calculation" in message


# --------------------------------------------------------------------------- #
# 2. Second iteration: a valid tool follows                                    #
# --------------------------------------------------------------------------- #


def test_unknown_tool_lets_model_pick_valid_tool_next_iteration() -> None:
    """Simulate a two-iteration loop:

    * Iteration 1: model requests ``Bash`` (unknown). Plugin returns a
      corrective dict that lists ``Calculation`` as available. Model reads
      the dict on the next LLM call and picks ``Calculation``.
    * Iteration 2: ``Calculation`` is a real tool, so ADK dispatches it
      normally. That happy path never enters ``on_tool_error_callback``, so
      the plugin must not interfere with real tools.

    We prove BOTH halves here:
    (a) iteration-1 corrective dict advertises ``Calculation``, and
    (b) iteration-2 (calling the plugin only if Calculation somehow ALSO
        raised, e.g. a bug) still soft-fails cleanly with the new tool's
        name, proving the plugin is stateless across tool names within the
        same budget.
    """
    plugin = MagiToolNotFoundSoftFailPlugin()
    ctx = _FakeCtx("inv-two-iter")

    first = _reflect(
        plugin,
        tool=_AdkPlaceholderTool("Bash"),
        tool_context=ctx,
        error=_adk_unknown_tool_error("Bash", ("Calculation", "FileRead")),
    )
    assert first is not None
    assert "Calculation" in first["available_tools"]

    # The model reads ``first`` and now picks ``Calculation``. ADK does NOT
    # invoke on_tool_error_callback for a real tool, so no assertion is
    # needed here for the happy path (ADK-owned, upstream-tested). But if a
    # real Calculation call itself raised (unrelated bug), the plugin must
    # NOT hijack it: it must return None and let the generic
    # tool-exception reflection handle it.
    second = _reflect(
        plugin,
        tool=_RealTool("Calculation", description="AST-based expression evaluator"),
        tool_context=ctx,
        error=ValueError("expression evaluation failed"),
    )
    assert second is None, (
        "the soft-fail plugin must only handle the ADK unknown-tool shape "
        "(placeholder BaseTool with description='Tool not found'); other "
        "raises stay owned by the generic tool-exception reflection path"
    )


# --------------------------------------------------------------------------- #
# 3. Default = unlimited: repeated unknown-tool never hard-fails               #
# --------------------------------------------------------------------------- #


def test_repeated_unknown_tool_never_hard_fails_by_default() -> None:
    """Kevin's direction: no hard-coded n-retry cap. In the default
    unlimited configuration (``attempt_cap == 0``), a model that keeps
    requesting an unknown tool 100 times MUST receive a corrective
    tool_result every single time; the plugin must never return ``None``
    for the ADK unknown-tool shape and must never let ADK re-raise. The
    turn-level iteration cap the runtime already enforces is the runaway
    backstop, not this plugin."""
    plugin = MagiToolNotFoundSoftFailPlugin()
    ctx = _FakeCtx("inv-unlimited")
    err = _adk_unknown_tool_error("Bash", ("Calculation",))

    results = [
        _reflect(
            plugin,
            tool=_AdkPlaceholderTool("Bash"),
            tool_context=ctx,
            error=err,
        )
        for _ in range(100)
    ]

    for index, result in enumerate(results, start=1):
        assert result is not None, (
            f"iteration {index}: default unlimited path must never return "
            "None for the ADK unknown-tool shape"
        )
        assert result["response_type"] == TOOL_NOT_FOUND_SOFT_FAIL_RESPONSE_TYPE
        assert result["error_code"] == TOOL_NOT_FOUND_ERROR_CODE
        # The default path must NEVER expose the exhausted terminal code as
        # a status; that code is reserved for the operator-opt-in cap path.
        assert result.get("error_code") != TOOL_NOT_FOUND_RETRY_EXHAUSTED_CODE
        assert result["retry_attempt"] == index
        assert "attempt_cap" not in result


# --------------------------------------------------------------------------- #
# 4. Operator opt-in cap fires at the configured N                             #
# --------------------------------------------------------------------------- #


def test_operator_can_opt_in_to_attempt_cap() -> None:
    """With ``MAGI_TOOL_NOT_FOUND_ATTEMPT_CAP=5`` explicitly set the plugin
    enforces a per-invocation cap of 5: iterations 1..5 return corrective
    dicts, iteration 6 returns ``None`` so ADK re-raises. Without the env
    (default ``0``) the cap does not fire at all, even after 100 calls."""

    # With the env explicitly set, cap fires at 5.
    env_capped = parse_tool_not_found_soft_fail_env({"MAGI_TOOL_NOT_FOUND_ATTEMPT_CAP": "5"})
    assert env_capped.enabled is True
    assert env_capped.attempt_cap == 5

    capped_plugin = build_tool_not_found_soft_fail_plugin(
        enabled=env_capped.enabled, attempt_cap=env_capped.attempt_cap
    )
    assert capped_plugin is not None
    ctx_capped = _FakeCtx("inv-capped")
    err = _adk_unknown_tool_error("Bash", ("Calculation",))
    capped_results = [
        _reflect(
            capped_plugin,
            tool=_AdkPlaceholderTool("Bash"),
            tool_context=ctx_capped,
            error=err,
        )
        for _ in range(6)
    ]
    for index in range(5):
        assert capped_results[index] is not None
        assert capped_results[index]["retry_attempt"] == index + 1
        # Operator explicitly opted in -> payload advertises the cap.
        assert capped_results[index]["attempt_cap"] == 5
    assert capped_results[5] is None, (
        "operator-imposed cap of 5 must fire on the 6th call (returns None so ADK re-raises)"
    )
    # And the plugin exposes the exhausted-code constant so the child runner
    # (or any wrapping trace) can label the terminal event distinctly.
    assert TOOL_NOT_FOUND_RETRY_EXHAUSTED_CODE == "child_llm_unknown_tool_retry_exhausted"

    # Without the env, the fake model can call 100 times with no cap.
    env_default = parse_tool_not_found_soft_fail_env({})
    assert env_default.enabled is True
    assert env_default.attempt_cap == 0, (
        "MAGI_TOOL_NOT_FOUND_ATTEMPT_CAP must default to 0 (unlimited) so "
        "retry policy is delegated to the model + the turn-level cap"
    )
    unlimited_plugin = build_tool_not_found_soft_fail_plugin(
        enabled=env_default.enabled, attempt_cap=env_default.attempt_cap
    )
    assert unlimited_plugin is not None
    ctx_unlimited = _FakeCtx("inv-unlimited-opt")
    unlimited_results = [
        _reflect(
            unlimited_plugin,
            tool=_AdkPlaceholderTool("Bash"),
            tool_context=ctx_unlimited,
            error=err,
        )
        for _ in range(100)
    ]
    for index, result in enumerate(unlimited_results, start=1):
        assert result is not None, (
            f"iteration {index}: without the opt-in env the plugin must "
            "never return None for the ADK unknown-tool shape"
        )
        assert "attempt_cap" not in result


# --------------------------------------------------------------------------- #
# 5. Flag OFF: hard-fail preserved byte-identical                              #
# --------------------------------------------------------------------------- #


def test_flag_off_preserves_hard_fail_byte_identical() -> None:
    """``MAGI_TOOL_NOT_FOUND_SOFT_FAIL=0`` disables the plugin build entirely,
    so the plane never registers it and ADK falls through to raise the
    original ValueError (today's behavior)."""
    env_off = parse_tool_not_found_soft_fail_env({"MAGI_TOOL_NOT_FOUND_SOFT_FAIL": "0"})
    assert env_off.enabled is False
    assert (
        build_tool_not_found_soft_fail_plugin(
            enabled=env_off.enabled, attempt_cap=env_off.attempt_cap
        )
        is None
    )


def test_flag_default_is_on_when_unset() -> None:
    """Default-ON is documented in the task spec as safe: the retry pool is
    bounded by the tools the runtime already exposes, so the plugin cannot
    escalate authority."""
    env_default = parse_tool_not_found_soft_fail_env({})
    assert env_default.enabled is True, (
        "MAGI_TOOL_NOT_FOUND_SOFT_FAIL must be default-ON so soft-fail is the "
        "out-of-the-box behavior; opt out via =0."
    )


def test_flag_explicit_on_matches_default() -> None:
    env_on = parse_tool_not_found_soft_fail_env({"MAGI_TOOL_NOT_FOUND_SOFT_FAIL": "1"})
    assert env_on.enabled is True
    plugin = build_tool_not_found_soft_fail_plugin(
        enabled=env_on.enabled, attempt_cap=env_on.attempt_cap
    )
    assert isinstance(plugin, MagiToolNotFoundSoftFailPlugin)


# --------------------------------------------------------------------------- #
# 6. Hard-fail path: available tools remain in the raised error                #
# --------------------------------------------------------------------------- #


def test_hard_fail_available_tools_still_listed_in_error() -> None:
    """When the plugin returns None (operator cap exhausted OR flag OFF),
    the original ValueError propagates. The test verifies the plugin never
    rewrites or hides the ADK error text: the operator/log still sees the
    full ``Available tools: ...`` list."""
    plugin = MagiToolNotFoundSoftFailPlugin(attempt_cap=1)
    ctx = _FakeCtx("inv-hard")

    err = _adk_unknown_tool_error("Bash", ("Calculation", "FileRead", "GitDiff"))

    # Under budget: soft-fail; original error text unchanged (plugin only READS it).
    first = _reflect(plugin, tool=_AdkPlaceholderTool("Bash"), tool_context=ctx, error=err)
    assert first is not None
    assert "Calculation" in str(err)
    assert "FileRead" in str(err)
    assert "GitDiff" in str(err)

    # Over budget: plugin returns None so ADK re-raises the SAME error
    # (identity check: no rewrapping / no message rewriting).
    second = _reflect(plugin, tool=_AdkPlaceholderTool("Bash"), tool_context=ctx, error=err)
    assert second is None
    assert "Available tools:" in str(err)
    assert "Calculation, FileRead, GitDiff" in str(err)


# --------------------------------------------------------------------------- #
# 7. Non-unknown-tool raises pass through untouched                            #
# --------------------------------------------------------------------------- #


def test_non_unknown_tool_error_returns_none() -> None:
    """Any error whose shape is NOT the ADK ``Tool '<name>' not found`` marker
    must return None so the generic tool-exception reflection plugin (or the
    edit-retry plugin) gets first crack. This keeps ordering strictly
    additive: soft-fail wins for its specific shape, everyone else is
    untouched."""
    plugin = MagiToolNotFoundSoftFailPlugin()
    ctx = _FakeCtx("inv-passthru")

    # A real tool with a genuine runtime raise: plugin ignores it.
    assert (
        _reflect(
            plugin,
            tool=_RealTool("Bash"),
            tool_context=ctx,
            error=RuntimeError("subprocess died"),
        )
        is None
    )

    # A ValueError whose text is unrelated to ADK's not-found shape: ignored.
    assert (
        _reflect(
            plugin,
            tool=_RealTool("FileEdit"),
            tool_context=ctx,
            error=ValueError("old_text_not_found"),
        )
        is None
    )


# --------------------------------------------------------------------------- #
# 8. Opt-in cap is scoped per invocation (not global across turns)             #
# --------------------------------------------------------------------------- #


def test_retry_budget_is_per_invocation() -> None:
    """When the operator opts in to a cap, the counter is scoped per
    invocation so it never leaks across turns."""
    plugin = MagiToolNotFoundSoftFailPlugin(attempt_cap=1)

    err = _adk_unknown_tool_error("Bash", ("Calculation",))
    first_turn_a = _reflect(
        plugin, tool=_AdkPlaceholderTool("Bash"), tool_context=_FakeCtx("inv-A"), error=err
    )
    second_turn_a = _reflect(
        plugin, tool=_AdkPlaceholderTool("Bash"), tool_context=_FakeCtx("inv-A"), error=err
    )
    # A different invocation starts with a fresh budget.
    first_turn_b = _reflect(
        plugin, tool=_AdkPlaceholderTool("Bash"), tool_context=_FakeCtx("inv-B"), error=err
    )

    assert first_turn_a is not None
    assert second_turn_a is None, "inv-A exhausted its opt-in cap of 1"
    assert first_turn_b is not None, "inv-B cap is independent"


def test_after_run_callback_sweeps_finished_invocation() -> None:
    """``after_run_callback`` clears the finished invocation's counters so
    the plugin cannot grow unbounded across turns."""
    plugin = MagiToolNotFoundSoftFailPlugin(attempt_cap=2)
    ctx = _FakeCtx("inv-sweep")

    err = _adk_unknown_tool_error("Bash", ("Calculation",))
    _reflect(plugin, tool=_AdkPlaceholderTool("Bash"), tool_context=ctx, error=err)
    _reflect(plugin, tool=_AdkPlaceholderTool("Bash"), tool_context=ctx, error=err)

    _run(plugin.after_run_callback(invocation_context=SimpleNamespace(invocation_id="inv-sweep")))

    # After sweep, the counter resets.
    after = _reflect(plugin, tool=_AdkPlaceholderTool("Bash"), tool_context=ctx, error=err)
    assert after is not None and after["retry_attempt"] == 1


# --------------------------------------------------------------------------- #
# 9. Builder / plugin construction                                             #
# --------------------------------------------------------------------------- #


def test_builder_returns_none_when_disabled() -> None:
    assert build_tool_not_found_soft_fail_plugin(enabled=False) is None
    # Explicit cap is also inert while the master switch is off.
    assert build_tool_not_found_soft_fail_plugin(enabled=False, attempt_cap=3) is None


def test_builder_returns_plugin_when_enabled_default_unlimited() -> None:
    """Default (no ``attempt_cap`` kwarg) means unlimited: the plugin builds
    with ``attempt_cap == 0`` and never terminates the corrective path."""
    plugin = build_tool_not_found_soft_fail_plugin(enabled=True)
    assert isinstance(plugin, MagiToolNotFoundSoftFailPlugin)
    assert plugin.attempt_cap == 0


def test_builder_returns_plugin_when_enabled_with_opt_in_cap() -> None:
    plugin = build_tool_not_found_soft_fail_plugin(enabled=True, attempt_cap=4)
    assert isinstance(plugin, MagiToolNotFoundSoftFailPlugin)
    assert plugin.attempt_cap == 4


def test_plugin_rejects_negative_cap() -> None:
    with pytest.raises(ValueError):
        MagiToolNotFoundSoftFailPlugin(attempt_cap=-1)


# --------------------------------------------------------------------------- #
# 10. Wiring: build_loop_resilience_controls includes the soft-fail control    #
# --------------------------------------------------------------------------- #


def test_wiring_registers_soft_fail_when_flag_on() -> None:
    """Under the loop-resilience builder with the default-ON flag, the
    plane exposes a control whose ``._plugin`` is our soft-fail plugin, so
    ``_ExtendedControlPlanePlugin`` can fan out ``on_tool_error_callback``
    into it."""
    from magi_agent.adk_bridge.control_plane import build_loop_resilience_controls

    controls = build_loop_resilience_controls({})
    plugin_types = [type(getattr(c, "_plugin", None)).__name__ for c in controls]
    assert "MagiToolNotFoundSoftFailPlugin" in plugin_types


def test_wiring_omits_soft_fail_when_flag_off() -> None:
    """With ``MAGI_TOOL_NOT_FOUND_SOFT_FAIL=0``, no soft-fail control is
    registered. Byte-identical to today's fail-fast behavior."""
    from magi_agent.adk_bridge.control_plane import build_loop_resilience_controls

    controls = build_loop_resilience_controls({"MAGI_TOOL_NOT_FOUND_SOFT_FAIL": "0"})
    plugin_types = [type(getattr(c, "_plugin", None)).__name__ for c in controls]
    assert "MagiToolNotFoundSoftFailPlugin" not in plugin_types


def test_wiring_soft_fail_registered_before_generic_reflection() -> None:
    """Fan-out is first-non-None-wins. The soft-fail control must appear
    BEFORE the generic tool-exception reflection control so unknown-tool
    errors are labelled with the specific ``tool_not_found`` code rather
    than the generic reflection response."""
    from magi_agent.adk_bridge.control_plane import build_loop_resilience_controls

    controls = build_loop_resilience_controls(
        {
            "MAGI_TOOL_NOT_FOUND_SOFT_FAIL": "1",
            "MAGI_TOOL_EXCEPTION_REFLECTION_ENABLED": "1",
        }
    )
    plugin_types = [type(getattr(c, "_plugin", None)).__name__ for c in controls]
    soft_fail_index = plugin_types.index("MagiToolNotFoundSoftFailPlugin")
    generic_index = plugin_types.index("MagiToolExceptionReflectionPlugin")
    assert soft_fail_index < generic_index, (
        "soft-fail must be first so it wins the first-non-None fan-out for "
        "the ADK unknown-tool shape; the generic reflection then handles any "
        "other raise the soft-fail path passes through."
    )
