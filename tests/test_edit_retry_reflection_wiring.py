"""PR2 integration tests: edit-failure reflection wiring on the live ADK Runner.

These tests drive a *real* ``google.adk.runners.Runner.run_async`` (not the
RetryController directly) so they prove the corrective hidden message is injected
through the actual ADK plugin/tool-callback path that the live turn engine uses.

A scripted fake LLM:
  turn 1 -> calls the FileEdit tool (which raises, simulating a match failure)
  turn 2+ -> records what the model received as the tool result, then either
             calls FileEdit again (to exhaust the budget) or finishes.

The plugin's ``on_tool_error_callback`` returns a replacement function response
carrying the corrective guidance, which ADK feeds back to the model as the tool
result on the next call. We assert that guidance reaches the model (turn 2).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from google.adk.agents import Agent
from google.adk.artifacts import InMemoryArtifactService
from google.adk.memory import InMemoryMemoryService
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.genai import types

from magi_agent.adk_bridge.edit_retry_reflection import (
    EDIT_RETRY_REFLECTION_RESPONSE_TYPE,
    MagiEditRetryReflectionPlugin,
)


_APP_NAME = "magi-edit-retry-itest"
_USER_ID = "user-edit-retry"


class _ScriptedEditLlm(BaseLlm):
    """Fake LLM that requests FileEdit, then inspects the returned tool result.

    ``edit_calls`` controls how many times the model attempts the edit before it
    finishes. ``tool_results_seen`` captures the text of every function_response
    the model received from the runner — this is where an injected corrective
    message appears.
    """

    model: str = "magi-scripted-edit-llm"
    edit_args: dict = {"path": "src/app.py", "oldText": "old", "newText": "new"}

    def model_post_init(self, _context: object) -> None:  # noqa: D401
        object.__setattr__(self, "_calls", 0)
        object.__setattr__(self, "tool_results_seen", [])
        object.__setattr__(self, "max_edit_attempts", 1)

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        object.__setattr__(self, "_calls", self._calls + 1)
        call_index = self._calls

        # Capture every function_response text the model can see this turn.
        for content in llm_request.contents or ():
            for part in content.parts or ():
                fr = getattr(part, "function_response", None)
                if fr is not None and fr.response is not None:
                    self.tool_results_seen.append(dict(fr.response))

        if call_index <= self.max_edit_attempts:
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part.from_function_call(
                            name="FileEdit", args=dict(self.edit_args)
                        )
                    ],
                )
            )
            return

        # Budget consumed (or model gives up): finish with plain text.
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(text="done")],
            )
        )


def _build_runner(plugin: MagiEditRetryReflectionPlugin | None) -> tuple[Runner, _ScriptedEditLlm]:
    llm = _ScriptedEditLlm()
    agent = Agent(
        name="magi_edit_retry_agent",
        model=llm,
        instruction="edit retry reflection integration test agent",
        tools=[FunctionTool(_file_edit_raises_named())],
    )
    runner = Runner(
        app_name=_APP_NAME,
        agent=agent,
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
        artifact_service=InMemoryArtifactService(),
        plugins=[plugin] if plugin is not None else None,
    )
    return runner, llm


def _file_edit_raises_named():
    # FunctionTool derives the tool name from __name__; the agent tool must be
    # named "FileEdit" so the plugin recognises it as an edit tool.
    def FileEdit(path: str = "", oldText: str = "", newText: str = "") -> dict:
        raise ValueError("old_text_not_found")

    return FileEdit


async def _run_turn(runner: Runner) -> list[object]:
    session = await runner.session_service.create_session(
        app_name=_APP_NAME, user_id=_USER_ID
    )
    events: list[object] = []
    async for event in runner.run_async(
        user_id=_USER_ID,
        session_id=session.id,
        new_message=types.Content(
            role="user", parts=[types.Part(text="edit the file")]
        ),
    ):
        events.append(event)
    return events


def test_flag_on_injects_corrective_message_into_next_model_turn() -> None:
    plugin = MagiEditRetryReflectionPlugin(max_attempts=2)
    runner, llm = _build_runner(plugin)

    asyncio.run(_run_turn(runner))

    # The model called FileEdit (raised) and was re-invoked with the injected
    # corrective tool result.
    injected = [
        r
        for r in llm.tool_results_seen
        if r.get("response_type") == EDIT_RETRY_REFLECTION_RESPONSE_TYPE
    ]
    assert injected, f"expected an injected corrective result, saw {llm.tool_results_seen}"
    guidance = injected[0]["reflection_guidance"]
    assert "FileEdit failed" in guidance
    assert "old_string was not found" in guidance
    assert injected[0]["error_type"] == "edit_apply_failed"
    assert injected[0]["retry_attempt"] == 1


def test_flag_off_does_not_inject_anything() -> None:
    # With no plugin attached the edit failure is NOT intercepted: ADK propagates
    # the original error and the model never sees a corrective tool result.
    runner, llm = _build_runner(None)

    raised = False
    try:
        asyncio.run(_run_turn(runner))
    except ValueError as exc:
        raised = exc.args[0] == "old_text_not_found"

    injected = [
        r
        for r in llm.tool_results_seen
        if isinstance(r, dict)
        and r.get("response_type") == EDIT_RETRY_REFLECTION_RESPONSE_TYPE
    ]
    assert injected == []
    assert raised is True


def test_fail_closed_after_max_attempts_stops_injecting() -> None:
    # max_attempts=1 means the controller aborts on the first attempt's budget
    # check (attempt >= max_attempts), so NO corrective message is injected and
    # the original error propagates.
    plugin = MagiEditRetryReflectionPlugin(max_attempts=1)
    runner, llm = _build_runner(plugin)
    # Let the model keep trying to edit so we'd see runaway injection if the
    # budget were not enforced.
    object.__setattr__(llm, "max_edit_attempts", 5)

    raised = False
    try:
        asyncio.run(_run_turn(runner))
    except ValueError as exc:
        raised = exc.args[0] == "old_text_not_found"

    injected = [
        r
        for r in llm.tool_results_seen
        if isinstance(r, dict)
        and r.get("response_type") == EDIT_RETRY_REFLECTION_RESPONSE_TYPE
    ]
    # Fail-closed: budget of 1 is immediately exhausted -> no injection, and the
    # original tool error is propagated rather than looping forever.
    assert injected == []
    assert raised is True


def test_pr1_old_text_not_unique_maps_to_not_unique_rule() -> None:
    # Forward-compat with PR1 (separate branch): when FileEdit raises
    # ValueError("old_text_not_unique"), the corrective message must be the
    # uniqueness-repair guidance, not the no-match guidance.
    from magi_agent.adk_bridge import edit_retry_reflection as mod

    plugin = MagiEditRetryReflectionPlugin(max_attempts=2)

    class _Tool:
        name = "FileEdit"

    class _Ctx:
        invocation_id = "inv-not-unique"

    result = asyncio.run(
        plugin.on_tool_error_callback(
            tool=_Tool(),
            tool_args={"path": "a.py", "oldText": "x", "newText": "y"},
            tool_context=_Ctx(),
            error=ValueError("old_text_not_unique"),
        )
    )
    assert result is not None
    assert result["error_code"] == "not_unique"
    assert "appears more than once" in result["reflection_guidance"]
    assert "surrounding context" in result["reflection_guidance"]
    _ = mod  # module import sanity


def test_lazy_placeholder_new_text_maps_to_lazy_output_rule() -> None:
    plugin = MagiEditRetryReflectionPlugin(max_attempts=2)

    class _Tool:
        name = "FileEdit"

    class _Ctx:
        invocation_id = "inv-lazy"

    result = asyncio.run(
        plugin.on_tool_error_callback(
            tool=_Tool(),
            tool_args={
                "path": "a.py",
                "oldText": "x",
                "newText": "def f():\n    # ... rest of the code unchanged",
            },
            tool_context=_Ctx(),
            error=ValueError("old_text_not_found"),
        )
    )
    assert result is not None
    assert result["error_code"] == "lazy_output"
    assert "placeholder comment" in result["reflection_guidance"]


def test_non_edit_tool_failure_is_ignored() -> None:
    plugin = MagiEditRetryReflectionPlugin(max_attempts=2)

    class _Tool:
        name = "Bash"

    class _Ctx:
        invocation_id = "inv-bash"

    result = asyncio.run(
        plugin.on_tool_error_callback(
            tool=_Tool(),
            tool_args={"command": "ls"},
            tool_context=_Ctx(),
            error=ValueError("nonzero_exit"),
        )
    )
    assert result is None


def test_env_flag_defaults_off_and_parses_budget() -> None:
    from magi_agent.config.env import parse_edit_retry_reflection_env

    off = parse_edit_retry_reflection_env({})
    assert off.enabled is False
    assert off.max_attempts == 2

    on = parse_edit_retry_reflection_env(
        {
            "MAGI_EDIT_RETRY_REFLECTION_ENABLED": "1",
            "MAGI_EDIT_RETRY_MAX_ATTEMPTS": "3",
        }
    )
    assert on.enabled is True
    assert on.max_attempts == 3


def test_env_flag_rejects_invalid_budget() -> None:
    import pytest

    from magi_agent.config.env import RuntimeEnvError, parse_edit_retry_reflection_env

    with pytest.raises(RuntimeEnvError):
        parse_edit_retry_reflection_env({"MAGI_EDIT_RETRY_MAX_ATTEMPTS": "0"})


def test_budget_two_injects_once_then_fails_closed() -> None:
    plugin = MagiEditRetryReflectionPlugin(max_attempts=2)
    runner, llm = _build_runner(plugin)
    object.__setattr__(llm, "max_edit_attempts", 5)

    raised = False
    try:
        asyncio.run(_run_turn(runner))
    except ValueError as exc:
        raised = exc.args[0] == "old_text_not_found"

    injected = [
        r
        for r in llm.tool_results_seen
        if isinstance(r, dict)
        and r.get("response_type") == EDIT_RETRY_REFLECTION_RESPONSE_TYPE
    ]
    # attempt 1 -> resample (inject); attempt 2 -> abort (fail closed).
    assert len(injected) == 1
    assert injected[0]["retry_attempt"] == 1
    assert raised is True
