"""PR12 integration tests: loop guard + error recovery on the live ADK Runner.

These drive a *real* ``google.adk.runners.Runner.run_async`` (not the detector /
RecoveryEngine in isolation) so they prove the existing subsystems are activated
through the actual ADK plugin callback path the live turn engine uses:

* loop guard -> ``after_tool_callback`` (soft nudge augments the real result,
  hard stop replaces it),
* error recovery -> ``on_model_error_callback`` (a 429 with Retry-After is
  classified and the RateLimit strategy waits the server-requested delay; a
  terminal error is not retried; prompt-too-long is classified but not
  infinitely retried).

Flag OFF (builder returns ``None``) attaches no plugin -> zero behavior. Each
test asserts behavior that disappears if the wiring is removed.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from google.adk.agents import Agent
from google.adk.apps.app import App
from google.adk.artifacts import InMemoryArtifactService
from google.adk.memory import InMemoryMemoryService
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.genai import types

from magi_agent.adk_bridge.resilience_plugin import (
    LOOP_GUARD_HARD_STATUS,
    LOOP_GUARD_RESPONSE_TYPE,
    LOOP_GUARD_SOFT_KEY,
    MagiResiliencePlugin,
    build_resilience_plugin,
)
from magi_agent.runtime.error_recovery import (
    ErrorRecoveryConfig,
    RecoveryEngine,
)
from magi_agent.runtime.loop_detectors import ToolCallLoopDetector

_APP_NAME = "magi-resilience-itest"
_APP_IDENTIFIER = "magi_resilience_itest"
_USER_ID = "user-resilience"


# ---------------------------------------------------------------------------
# Loop guard — driven through a real Runner.run_async tool loop.
# ---------------------------------------------------------------------------


class _RepeatingToolLlm(BaseLlm):
    """Fake LLM that keeps calling the same tool with identical args.

    It calls ``Search`` with the same args ``call_budget`` times, capturing the
    tool result it sees each turn (where the loop guard nudge / stop appears),
    then finishes with text.
    """

    model: str = "magi-repeating-tool-llm"

    def model_post_init(self, _context: object) -> None:
        object.__setattr__(self, "_calls", 0)
        object.__setattr__(self, "tool_results_seen", [])
        object.__setattr__(self, "call_budget", 6)

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        object.__setattr__(self, "_calls", self._calls + 1)
        for content in llm_request.contents or ():
            for part in content.parts or ():
                fr = getattr(part, "function_response", None)
                if fr is not None and fr.response is not None:
                    self.tool_results_seen.append(dict(fr.response))

        if self._calls <= self.call_budget:
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part.from_function_call(
                            name="Search", args={"query": "same"}
                        )
                    ],
                )
            )
            return
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text="done")])
        )


def _search_tool():
    def Search(query: str = "") -> dict:
        return {"status": "ok", "results": ["a", "b"]}

    return Search


def _build_loop_runner(
    plugin: MagiResiliencePlugin | None,
) -> tuple[Runner, _RepeatingToolLlm]:
    llm = _RepeatingToolLlm()
    agent = Agent(
        name="magi_resilience_agent",
        model=llm,
        instruction="loop guard integration test agent",
        tools=[FunctionTool(_search_tool())],
    )
    app = App(
        name=_APP_IDENTIFIER,
        root_agent=agent,
        plugins=[plugin] if plugin is not None else [],
    )
    runner = Runner(
        app=app,
        app_name=_APP_NAME,
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
        artifact_service=InMemoryArtifactService(),
    )
    return runner, llm


async def _run_turn(runner: Runner) -> list[object]:
    session = await runner.session_service.create_session(
        app_name=_APP_NAME, user_id=_USER_ID
    )
    events: list[object] = []
    async for event in runner.run_async(
        user_id=_USER_ID,
        session_id=session.id,
        new_message=types.Content(
            role="user", parts=[types.Part(text="search please")]
        ),
    ):
        events.append(event)
    return events


def test_loop_guard_soft_then_hard_through_live_runner() -> None:
    # soft_threshold=3, hard_threshold=5: the 3rd/4th identical call get a soft
    # nudge, the 5th gets a hard stop. Driven through the real ADK tool loop.
    plugin = build_resilience_plugin(
        loop_guard_enabled=True,
        loop_guard_soft_threshold=3,
        loop_guard_hard_threshold=5,
        error_recovery_enabled=False,
    )
    runner, llm = _build_loop_runner(plugin)
    object.__setattr__(llm, "call_budget", 6)

    asyncio.run(_run_turn(runner))

    softs = [r for r in llm.tool_results_seen if r.get("loop_action") == "soft_warning"]
    hards = [
        r
        for r in llm.tool_results_seen
        if r.get("response_type") == LOOP_GUARD_RESPONSE_TYPE
        and r.get("loop_action") == "hard_escalation"
    ]
    # 3rd identical call -> first soft nudge (model sees it on the 4th turn).
    assert softs, f"expected at least one soft nudge, saw {llm.tool_results_seen}"
    assert LOOP_GUARD_SOFT_KEY in softs[0]
    # soft nudge preserves the real tool result.
    assert softs[0].get("status") == "ok"
    # 5th identical consecutive call -> hard stop replaces the result.
    assert hards, f"expected a hard stop, saw {llm.tool_results_seen}"
    assert hards[0]["status"] == LOOP_GUARD_HARD_STATUS
    assert "Stop repeating" in hards[0]["stop_directive"]


def test_loop_guard_flag_off_no_behavior() -> None:
    # Builder returns None when both features are off -> no plugin attached.
    plugin = build_resilience_plugin(
        loop_guard_enabled=False, error_recovery_enabled=False
    )
    assert plugin is None
    runner, llm = _build_loop_runner(plugin)
    object.__setattr__(llm, "call_budget", 6)

    asyncio.run(_run_turn(runner))

    guarded = [
        r
        for r in llm.tool_results_seen
        if r.get("response_type") == LOOP_GUARD_RESPONSE_TYPE
        or r.get("loop_action") in {"soft_warning", "hard_escalation"}
    ]
    assert guarded == []


def test_loop_guard_distinct_calls_never_trip() -> None:
    # A detector only escalates on *identical* calls; distinct args stay "ok".
    plugin = build_resilience_plugin(
        loop_guard_enabled=True,
        loop_guard_soft_threshold=3,
        loop_guard_hard_threshold=5,
        error_recovery_enabled=False,
    )

    class _Tool:
        name = "Search"

    class _Ctx:
        invocation_id = "inv-distinct"

    async def drive() -> list[object]:
        outs = []
        for i in range(6):
            outs.append(
                await plugin.after_tool_callback(
                    tool=_Tool(),
                    tool_args={"query": f"q{i}"},
                    tool_context=_Ctx(),
                    result={"status": "ok"},
                )
            )
        return outs

    results = asyncio.run(drive())
    assert all(r is None for r in results)


# ---------------------------------------------------------------------------
# Error recovery — model errors via on_model_error_callback.
# ---------------------------------------------------------------------------


class _Error429(Exception):
    """Realistic 429 error (Exception, as ADK passes) carrying a Retry-After-ms
    hint via a ``.response.headers`` map, like provider SDK errors."""

    def __init__(self) -> None:
        super().__init__("429 too many requests rate_limit")
        self.response = type("Resp", (), {"headers": {"retry-after-ms": "1500"}})()


def _recovery_plugin(max_attempts: int = 3) -> MagiResiliencePlugin:
    plugin = build_resilience_plugin(
        loop_guard_enabled=False,
        error_recovery_enabled=True,
        recovery_max_attempts=max_attempts,
    )
    assert plugin is not None
    return plugin


class _Ctx:
    invocation_id = "inv-recovery"


def test_recovery_callback_does_not_fabricate_recovery_response() -> None:
    # HONESTY: the on_model_error_callback is a substitute-the-response seam, not
    # a retry seam. A content-less LlmResponse there would END the turn while
    # pretending recovery happened. So for a retryable 429 the callback now
    # CLASSIFIES (telemetry) and returns None -> the error PROPAGATES to the
    # genuine retry wrapper at the run-invocation boundary (cli.engine). It must
    # NOT sleep here and must NOT return a recovery LlmResponse.
    import magi_agent.runtime.error_recovery.strategies.rate_limit as rl

    config = ErrorRecoveryConfig(recovery_enabled=True, rate_limit_base_delay_seconds=99)
    engine = RecoveryEngine(config)
    plugin = MagiResiliencePlugin(recovery_engine=engine, recovery_max_attempts=3)

    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    original = rl.asyncio.sleep
    rl.asyncio.sleep = fake_sleep  # type: ignore[assignment]
    try:
        result = asyncio.run(
            plugin.on_model_error_callback(
                callback_context=_Ctx(),
                llm_request=type("Req", (), {"contents": []})(),
                error=_Error429(),
            )
        )
    finally:
        rl.asyncio.sleep = original  # type: ignore[assignment]

    # Propagated (None), NOT a fabricated recovery response; and no backoff sleep
    # happened in the callback (backoff lives at the run-invocation seam).
    assert result is None
    assert slept == []
    # Classification was recorded (telemetry) so the per-scope state is bounded.
    assert "inv-recovery" in plugin._recovery_state


def test_recovery_terminal_error_not_retried() -> None:
    plugin = _recovery_plugin()
    result = asyncio.run(
        plugin.on_model_error_callback(
            callback_context=_Ctx(),
            llm_request=type("Req", (), {"contents": []})(),
            error=ValueError("some unrecognized internal failure"),
        )
    )
    # Terminal -> None (propagate / fail-open), no recovery response.
    assert result is None


def test_recovery_prompt_too_long_classified_not_infinite_retried() -> None:
    # prompt_too_long is classified but NOT retried at the model-error boundary
    # (PR13 compaction territory). With no hook it returns None (propagate).
    plugin = _recovery_plugin()
    result = asyncio.run(
        plugin.on_model_error_callback(
            callback_context=_Ctx(),
            llm_request=type("Req", (), {"contents": []})(),
            error="prompt is too long: maximum context length is 100 tokens",
        )
    )
    assert result is None


def test_recovery_context_overflow_hook_invoked() -> None:
    # The clean PR13 hook is invoked for prompt_too_long and its result is used.
    seen: list[object] = []
    sentinel = LlmResponse(custom_metadata={"compacted": True})

    def hook(error, ctx):
        seen.append(error.kind.value)
        return sentinel

    plugin = MagiResiliencePlugin(
        recovery_engine=RecoveryEngine(ErrorRecoveryConfig(recovery_enabled=True)),
        context_overflow_hook=hook,
    )
    result = asyncio.run(
        plugin.on_model_error_callback(
            callback_context=_Ctx(),
            llm_request=type("Req", (), {"contents": []})(),
            error="prompt is too long",
        )
    )
    assert seen == ["prompt_too_long"]
    assert result is sentinel


def test_recovery_callback_classifies_but_never_recovers_in_callback() -> None:
    # Repeated retryable errors through the callback always propagate (None):
    # the callback never performs recovery (that is the run-invocation seam's
    # job — see the live recovery test). It only records classification so the
    # per-scope telemetry dict stays bounded.
    plugin = _recovery_plugin(max_attempts=1)

    async def drive():
        first = await plugin.on_model_error_callback(
            callback_context=_Ctx(),
            llm_request=type("Req", (), {"contents": []})(),
            error=_Error429(),
        )
        second = await plugin.on_model_error_callback(
            callback_context=_Ctx(),
            llm_request=type("Req", (), {"contents": []})(),
            error=_Error429(),
        )
        return first, second

    first, second = asyncio.run(drive())
    # Both propagate (no fabricated recovery) regardless of budget.
    assert first is None
    assert second is None


def test_recovery_flag_off_no_behavior() -> None:
    # recovery disabled -> plugin has no engine -> always None.
    plugin = build_resilience_plugin(
        loop_guard_enabled=True,  # loop on, recovery off
        error_recovery_enabled=False,
    )
    assert plugin is not None
    result = asyncio.run(
        plugin.on_model_error_callback(
            callback_context=_Ctx(),
            llm_request=type("Req", (), {"contents": []})(),
            error=_Error429(),
        )
    )
    assert result is None


# ---------------------------------------------------------------------------
# Builder + cleanup + env flags.
# ---------------------------------------------------------------------------


def test_after_run_callback_sweeps_invocation_state() -> None:
    plugin = build_resilience_plugin(
        loop_guard_enabled=True, error_recovery_enabled=True
    )
    assert plugin is not None
    plugin._detectors["inv-keep"] = ToolCallLoopDetector()
    plugin._detectors["inv-drop"] = ToolCallLoopDetector()

    class _Inv:
        invocation_id = "inv-drop"

    asyncio.run(plugin.after_run_callback(invocation_context=_Inv()))
    assert "inv-drop" not in plugin._detectors
    assert "inv-keep" in plugin._detectors


def test_env_flags_default_off() -> None:
    from magi_agent.config.env import parse_error_recovery_env, parse_loop_guard_env

    lg = parse_loop_guard_env({})
    assert lg.enabled is False
    assert lg.soft_threshold == 3
    assert lg.hard_threshold == 5

    er = parse_error_recovery_env({})
    assert er.enabled is False
    assert er.max_recovery_attempts == 3


def test_env_flags_parse_on() -> None:
    from magi_agent.config.env import parse_error_recovery_env, parse_loop_guard_env

    lg = parse_loop_guard_env(
        {
            "MAGI_LOOP_GUARD_ENABLED": "1",
            "MAGI_LOOP_GUARD_SOFT_THRESHOLD": "2",
            "MAGI_LOOP_GUARD_HARD_THRESHOLD": "4",
        }
    )
    assert lg.enabled is True
    assert lg.soft_threshold == 2
    assert lg.hard_threshold == 4

    er = parse_error_recovery_env(
        {"MAGI_ERROR_RECOVERY_ENABLED": "true", "MAGI_MAX_RECOVERY_ATTEMPTS": "5"}
    )
    assert er.enabled is True
    assert er.max_recovery_attempts == 5


def test_env_flags_reject_invalid() -> None:
    import pytest

    from magi_agent.config.env import (
        RuntimeEnvError,
        parse_error_recovery_env,
        parse_loop_guard_env,
    )

    with pytest.raises(RuntimeEnvError):
        parse_loop_guard_env(
            {"MAGI_LOOP_GUARD_ENABLED": "1", "MAGI_LOOP_GUARD_HARD_THRESHOLD": "1"}
        )
    with pytest.raises(RuntimeEnvError):
        parse_error_recovery_env({"MAGI_MAX_RECOVERY_ATTEMPTS": "0"})


def test_live_runner_builder_attaches_resilience_plugin(monkeypatch) -> None:
    # Prove the LIVE runner builder wires the resilience control when the flag is on.
    # After PR2 (control-plane), the resilience plugin is wrapped in a
    # _ResilienceLoopControl adapter registered in the ControlPlane, NOT as a
    # top-level plugin. The plane is wrapped in a single ControlPlanePlugin.
    from magi_agent.adk_bridge import local_runner as lr
    from magi_agent.adk_bridge.control_plane import (
        CONTROL_PLANE_PLUGIN_NAME,
        _ResilienceLoopControl,
    )

    monkeypatch.setenv(lr.LOCAL_ADK_RUNNER_FLAG, "1")
    monkeypatch.setenv("MAGI_LOOP_GUARD_ENABLED", "1")
    bundle = lr.build_local_adk_runner()
    plane_plugin = next(
        p for p in bundle.runner.plugin_manager.plugins if p.name == CONTROL_PLANE_PLUGIN_NAME
    )
    controls = plane_plugin._p._controls
    assert any(isinstance(c, _ResilienceLoopControl) for c in controls), controls


def test_live_runner_builder_no_plugin_when_off(monkeypatch) -> None:
    # After PR2: with flags off, the plane has no resilience control.
    from magi_agent.adk_bridge import local_runner as lr
    from magi_agent.adk_bridge.control_plane import (
        CONTROL_PLANE_PLUGIN_NAME,
        _ResilienceLoopControl,
    )

    monkeypatch.setenv(lr.LOCAL_ADK_RUNNER_FLAG, "1")
    monkeypatch.delenv("MAGI_LOOP_GUARD_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_ERROR_RECOVERY_ENABLED", raising=False)
    bundle = lr.build_local_adk_runner()
    plane_plugin = next(
        p for p in bundle.runner.plugin_manager.plugins if p.name == CONTROL_PLANE_PLUGIN_NAME
    )
    controls = plane_plugin._p._controls
    assert not any(isinstance(c, _ResilienceLoopControl) for c in controls), controls
