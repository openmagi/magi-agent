import asyncio
import subprocess
import sys

import pytest
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.genai import types
from pydantic import ValidationError

from magi_agent.adk_bridge.runner_adapter import (
    ADK_RUNNER_KWARG_ALLOWLIST,
    OpenMagiRunnerAdapter,
    RunnerTurnInput,
)
from magi_agent.harness.resolved import build_default_resolved_harness_state


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run_async(self, **kwargs: object):
        self.calls.append(kwargs)
        yield {"type": "fake_adk_event", "text": "hi"}


FORBIDDEN_OPENMAGI_RUNNER_VALUES = (
    "harness_state",
    "harnessState",
    "state_delta",
    "stateDelta",
    "run_config",
    "runConfig",
    "openmagi.currentTurnId",
    "evidenceContracts",
    "control",
    "trafficAttached",
    "executionAttached",
)
FORBIDDEN_OPENMAGI_MESSAGE_KEYS = (
    "openmagi",
    "openMagi",
    "OpenMagi",
    "open-magi",
    "open_magi",
    "state_delta",
    "stateDelta",
    "state-delta",
    "openmagi.state_delta",
    "openmagi.stateDelta",
    "openmagi.state-delta",
    "openmagi_state_delta",
    "openmagi-state-delta",
    "openmagiStateDelta",
    "openMagi.state_delta",
    "OpenMagi.stateDelta",
    "open-magi.state-delta",
    "open_magi.state_delta",
    "openMagiStateDelta",
    "OpenMagiStateDelta",
    "open_magi_state_delta",
    "open-magi-state-delta",
    "run_config",
    "runConfig",
    "openmagi.run_config",
    "openmagi.runConfig",
    "openMagi.runConfig",
    "open_magi_run_config",
    "traffic_attached",
    "trafficAttached",
    "openmagi.traffic_attached",
    "openmagi.trafficAttached",
    "openMagiTrafficAttached",
    "open_magi_traffic_attached",
    "execution_attached",
    "executionAttached",
    "openmagi.execution_attached",
    "openmagi.executionAttached",
    "openMagiExecutionAttached",
    "open_magi_execution_attached",
    "openmagi.harness",
    "openMagi.harness",
    "open_magi_harness",
    "harness_state",
    "harnessState",
    "openmagi.harness_state",
    "openmagi.harnessState",
    "openMagiHarnessState",
    "open_magi_harness_state",
    "openmagi.evidence",
    "openMagi.evidence",
    "open_magi_evidence",
    "evidence_contracts",
    "evidenceContracts",
    "openmagi.evidence_contracts",
    "openmagi.evidenceContracts",
    "openMagiEvidenceContracts",
    "open_magi_evidence_contracts",
    "control",
    "openmagi.control",
    "openMagi.control",
    "open_magi_control",
    "openmagi.state",
    "openMagi.state",
    "open_magi_state",
    "current_turn_id",
    "currentTurnId",
    "openmagi.current_turn_id",
    "openmagi.currentTurnId",
    "openMagiCurrentTurnId",
    "open_magi_current_turn_id",
)
FORBIDDEN_OPENMAGI_OBJECT_CHILD_KEYS = (
    "state",
    "harness",
    "evidence",
    "control",
    "run_config",
    "runConfig",
    "harness-state",
    "state_delta",
    "stateDelta",
    "state-delta",
    "traffic_attached",
    "trafficAttached",
    "execution_attached",
    "executionAttached",
)
FORBIDDEN_BRAND_SEPARATED_OPENMAGI_KEYS = (
    "openMagi",
    "OpenMagi",
    "open-magi",
    "open_magi",
    "openMagiStateDelta",
    "OpenMagiStateDelta",
    "open_magi_state_delta",
    "open-magi-state-delta",
    "openMagi.runConfig",
    "open_magi_run_config",
    "openMagiTrafficAttached",
    "open_magi_execution_attached",
    "openMagiHarnessState",
    "open_magi_evidence_contracts",
    "openMagiCurrentTurnId",
)
FORBIDDEN_UNKNOWN_OPENMAGI_NAMESPACE_KEYS = (
    "openmagi.foo",
    "openMagiFoo",
    "open_magi_foo",
    "open-magi-foo",
)
FORBIDDEN_COLLAPSED_OPENMAGI_KEYS = (
    "openmagistatedelta",
    "openmagirunconfig",
    "openmagievidence",
    "openmagicontrol",
    "openmagifoo",
)


def _content(text: str = "hello") -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=text)])


def _flatten_values(value: object) -> list[object]:
    if isinstance(value, dict):
        flattened: list[object] = []
        for key, nested_value in value.items():
            flattened.append(key)
            flattened.extend(_flatten_values(nested_value))
        return flattened
    if isinstance(value, (list, tuple, set, frozenset)):
        flattened = []
        for item in value:
            flattened.extend(_flatten_values(item))
        return flattened
    return [value]


def test_runner_adapter_exposes_explicit_adk_kwarg_allowlist() -> None:
    assert ADK_RUNNER_KWARG_ALLOWLIST == frozenset(
        {"user_id", "session_id", "invocation_id", "new_message"}
    )


def test_runner_adapter_import_does_not_load_evidence_or_heavy_harness_modules() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("magi_agent.adk_bridge.runner_adapter")
assert hasattr(module, "OpenMagiRunnerAdapter")
assert hasattr(module, "RunnerTurnInput")

forbidden = (
    "magi_agent.evidence",
    "magi_agent.harness.audit",
    "magi_agent.harness.engine",
    "magi_agent.harness.resolved",
    "magi_agent.hooks",
)
loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
]
if loaded:
    raise AssertionError(f"runner_adapter loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_runner_adapter_calls_adk_runner_without_leaking_harness_state() -> None:
    runner = FakeRunner()
    turn_harness = build_default_resolved_harness_state(agent_role="coding", spawn_depth=1)
    adapter = OpenMagiRunnerAdapter(runner=runner)

    async def collect() -> list[object]:
        return [
            event
            async for event in adapter.run_turn(
                RunnerTurnInput(
                    user_id="user-1",
                    session_id="agent:main:app:default",
                    turn_id="turn-1",
                    invocation_id="turn-1",
                    new_message=_content(),
                    state_delta={"openmagi.currentTurnId": "turn-1"},
                    run_config={"openmagi": {"control": "local-only"}},
                    harness_state=turn_harness,
                )
            )
        ]

    events = asyncio.run(collect())
    assert events == [{"type": "fake_adk_event", "text": "hi"}]
    call = runner.calls[0]
    # Core identity keys must be present and correct.
    assert call["user_id"] == "user-1"
    assert call["session_id"] == "agent:main:app:default"
    assert call["invocation_id"] == "turn-1"
    assert call["new_message"] == _content()
    # Adapter injects its own streaming RunConfig — caller's run_config must NOT
    # be passed (side-channel block), but the adapter-owned one may appear.
    assert "harness_state" not in call
    assert "harnessState" not in call
    assert "state_delta" not in call
    assert "stateDelta" not in call
    assert "runConfig" not in call
    # The adapter-injected run_config is the only allowed one and must be
    # an SSE RunConfig (not the caller-supplied dict {"openmagi": ...}).
    if "run_config" in call:
        assert isinstance(call["run_config"], RunConfig)
        assert call["run_config"].streaming_mode == StreamingMode.SSE
    assert not hasattr(adapter, "harness_state")
    assert turn_harness.agent_role == "coding"


def test_runner_adapter_blocks_openmagi_state_delta_and_run_config_leakage() -> None:
    runner = FakeRunner()
    turn_harness = build_default_resolved_harness_state(agent_role="coding", spawn_depth=1)
    adapter = OpenMagiRunnerAdapter(runner=runner)

    async def collect() -> list[object]:
        return [
            event
            async for event in adapter.run_turn(
                RunnerTurnInput(
                    user_id="user-1",
                    session_id="agent:main:app:default",
                    turn_id="turn-1",
                    invocation_id="turn-1",
                    new_message=_content(),
                    state_delta={
                        "openmagi.currentTurnId": "turn-1",
                        "openmagi.harness": {"mode": "audit"},
                        "evidenceContracts": ["coding-basic"],
                        "control": {"trafficAttached": False},
                        "arbitrary": "must-not-pass",
                    },
                    run_config={
                        "temperature": 0.2,
                        "openmagi": {"evidence": {"contract": "coding-basic"}},
                    },
                    harness_state=turn_harness,
                )
            )
        ]

    asyncio.run(collect())

    call = runner.calls[0]
    # Adapter-owned run_config (SSE) may appear in addition to the allowlist keys.
    # Caller-supplied openmagi-laden run_config must NOT leak through.
    allowed_keys = ADK_RUNNER_KWARG_ALLOWLIST | {"run_config"}
    assert set(call) <= allowed_keys
    assert ADK_RUNNER_KWARG_ALLOWLIST <= set(call)
    # The adapter-owned run_config must be the SSE RunConfig, not the caller dict.
    if "run_config" in call:
        assert isinstance(call["run_config"], RunConfig)
        assert call["run_config"].streaming_mode == StreamingMode.SSE
    flattened_call = _flatten_values(call)
    # Forbidden openmagi runtime values must not appear anywhere in the call.
    # "run_config" itself is an allowed key name (adapter-owned), so we skip it
    # from the forbidden list for the flattened check.
    openmagi_forbidden = [v for v in FORBIDDEN_OPENMAGI_RUNNER_VALUES if v != "run_config"]
    for forbidden in openmagi_forbidden:
        assert forbidden not in flattened_call


def test_runner_adapter_does_not_depend_on_shared_harness_state_between_turns() -> None:
    runner = FakeRunner()
    adapter = OpenMagiRunnerAdapter(runner=runner)
    coding_harness = build_default_resolved_harness_state(agent_role="coding", spawn_depth=1)
    research_harness = build_default_resolved_harness_state(agent_role="research", spawn_depth=2)

    async def collect(turn_id: str, harness_state: object) -> list[object]:
        return [
            event
            async for event in adapter.run_turn(
                RunnerTurnInput(
                    user_id="user-1",
                    session_id="agent:main:app:default",
                    turn_id=turn_id,
                    invocation_id=turn_id,
                    new_message=_content(turn_id),
                    harness_state=harness_state,
                )
            )
        ]

    async def collect_both() -> list[list[object]]:
        return await asyncio.gather(
            collect("turn-coding", coding_harness),
            collect("turn-research", research_harness),
        )

    results = asyncio.run(collect_both())

    assert results == [
        [{"type": "fake_adk_event", "text": "hi"}],
        [{"type": "fake_adk_event", "text": "hi"}],
    ]
    assert [call["invocation_id"] for call in runner.calls] == ["turn-coding", "turn-research"]
    assert all("harness_state" not in call for call in runner.calls)
    assert all("harnessState" not in call for call in runner.calls)
    assert not hasattr(adapter, "harness_state")


def test_runner_adapter_collect_events_iterates_run_turn_without_bypassing_runner() -> None:
    runner = FakeRunner()
    turn_harness = build_default_resolved_harness_state(agent_role="coding", spawn_depth=1)
    adapter = OpenMagiRunnerAdapter(runner=runner)
    turn_input = RunnerTurnInput(
        user_id="user-1",
        session_id="agent:main:app:default",
        turn_id="turn-1",
        invocation_id="turn-1",
        new_message=_content(),
        state_delta={"openmagi.currentTurnId": "turn-1"},
        run_config={"openmagi": {"control": "local-only"}},
        harness_state=turn_harness,
    )

    events = asyncio.run(adapter.collect_events(turn_input))

    assert events == [{"type": "fake_adk_event", "text": "hi"}]
    assert len(runner.calls) == 1
    # Adapter-owned SSE run_config may also be present.
    allowed_keys = ADK_RUNNER_KWARG_ALLOWLIST | {"run_config"}
    assert set(runner.calls[0]) <= allowed_keys
    assert ADK_RUNNER_KWARG_ALLOWLIST <= set(runner.calls[0])


@pytest.mark.parametrize(
    "new_message",
    (
        {"role": "user", "parts": [{"text": "hello"}]},
        [{"role": "user", "parts": [{"text": "hello"}]}],
        object(),
    ),
)
def test_runner_turn_input_requires_official_adk_content(new_message: object) -> None:
    with pytest.raises(ValidationError, match="Content"):
        RunnerTurnInput(
            user_id="user-1",
            session_id="agent:main:app:default",
            turn_id="turn-1",
            invocation_id="turn-1",
            new_message=new_message,
            harness_state=build_default_resolved_harness_state(
                agent_role="coding",
                spawn_depth=1,
            ),
        )


@pytest.mark.parametrize("forbidden_key", FORBIDDEN_OPENMAGI_MESSAGE_KEYS)
def test_runner_turn_input_rejects_openmagi_keys_nested_in_new_message(
    forbidden_key: str,
) -> None:
    with pytest.raises(ValidationError, match=forbidden_key):
        RunnerTurnInput(
            user_id="user-1",
            session_id="agent:main:app:default",
            turn_id="turn-1",
            invocation_id="turn-1",
            new_message=types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            name="leak",
                            args={forbidden_key: "must-not-pass"},
                        )
                    )
                ],
            ),
            harness_state=build_default_resolved_harness_state(
                agent_role="coding",
                spawn_depth=1,
            ),
        )


@pytest.mark.parametrize("forbidden_key", FORBIDDEN_UNKNOWN_OPENMAGI_NAMESPACE_KEYS)
def test_runner_turn_input_rejects_unknown_openmagi_namespace_keys_nested_in_new_message(
    forbidden_key: str,
) -> None:
    with pytest.raises(ValidationError, match="forbidden OpenMagi key"):
        RunnerTurnInput(
            user_id="user-1",
            session_id="agent:main:app:default",
            turn_id="turn-1",
            invocation_id="turn-1",
            new_message=types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            name="leak",
                            args={forbidden_key: "must-not-pass"},
                        )
                    )
                ],
            ),
            harness_state=build_default_resolved_harness_state(
                agent_role="coding",
                spawn_depth=1,
            ),
        )


@pytest.mark.parametrize("forbidden_key", FORBIDDEN_COLLAPSED_OPENMAGI_KEYS)
def test_runner_turn_input_rejects_collapsed_openmagi_keys_nested_in_new_message(
    forbidden_key: str,
) -> None:
    with pytest.raises(ValidationError, match="forbidden OpenMagi key"):
        RunnerTurnInput(
            user_id="user-1",
            session_id="agent:main:app:default",
            turn_id="turn-1",
            invocation_id="turn-1",
            new_message=types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            name="leak",
                            args={forbidden_key: "must-not-pass"},
                        )
                    )
                ],
            ),
            harness_state=build_default_resolved_harness_state(
                agent_role="coding",
                spawn_depth=1,
            ),
        )


@pytest.mark.parametrize("forbidden_child_key", FORBIDDEN_OPENMAGI_OBJECT_CHILD_KEYS)
def test_runner_turn_input_rejects_openmagi_object_payloads_nested_in_function_call_args(
    forbidden_child_key: str,
) -> None:
    with pytest.raises(ValidationError, match=forbidden_child_key):
        RunnerTurnInput(
            user_id="user-1",
            session_id="agent:main:app:default",
            turn_id="turn-1",
            invocation_id="turn-1",
            new_message=types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            name="leak",
                            args={"openmagi": {forbidden_child_key: {"secret": True}}},
                        )
                    )
                ],
            ),
            harness_state=build_default_resolved_harness_state(
                agent_role="coding",
                spawn_depth=1,
            ),
        )


@pytest.mark.parametrize(
    "payload",
    (
        pytest.param({"not-json"}, id="set"),
        pytest.param(b"not-json", id="bytes"),
        pytest.param(bytearray(b"not-json"), id="bytearray"),
        pytest.param(1 + 2j, id="complex"),
        pytest.param(("not", "json"), id="tuple"),
        pytest.param(float("inf"), id="non-finite-float"),
        pytest.param({1: "not-json"}, id="non-string-dict-key"),
        pytest.param(object(), id="arbitrary-object"),
    ),
)
def test_runner_adapter_rejects_non_json_function_call_args_before_runner_call(
    payload: object,
) -> None:
    runner = FakeRunner()
    adapter = OpenMagiRunnerAdapter(runner=runner)
    new_message = types.Content(
        role="user",
        parts=[
            types.Part(
                function_call=types.FunctionCall(
                    name="valid",
                    args={"payload": "initial"},
                )
            )
        ],
    )
    turn_input = RunnerTurnInput(
        user_id="user-1",
        session_id="agent:main:app:default",
        turn_id="turn-1",
        invocation_id="turn-1",
        new_message=new_message,
        harness_state=build_default_resolved_harness_state(
            agent_role="coding",
            spawn_depth=1,
        ),
    )

    assert turn_input.new_message.parts is not None
    function_call = turn_input.new_message.parts[0].function_call
    assert function_call is not None
    assert function_call.args is not None
    function_call.args["payload"] = payload

    with pytest.raises(ValueError, match="JSON"):
        asyncio.run(adapter.collect_events(turn_input))

    assert runner.calls == []


@pytest.mark.parametrize(
    "forbidden_key",
    (
        "openmagi.currentTurnId",
        "control",
        "state-delta",
        "openmagi_state_delta",
        "openmagi.state-delta",
        "openmagi-state-delta",
        "openmagiStateDelta",
    ),
)
def test_runner_adapter_revalidates_mutable_new_message_before_runner_call(
    forbidden_key: str,
) -> None:
    runner = FakeRunner()
    adapter = OpenMagiRunnerAdapter(runner=runner)
    new_message = types.Content(
        role="user",
        parts=[
            types.Part(
                function_call=types.FunctionCall(
                    name="valid",
                    args={"allowed": "initial"},
                )
            )
        ],
    )
    turn_input = RunnerTurnInput(
        user_id="user-1",
        session_id="agent:main:app:default",
        turn_id="turn-1",
        invocation_id="turn-1",
        new_message=new_message,
        harness_state=build_default_resolved_harness_state(
            agent_role="coding",
            spawn_depth=1,
        ),
    )

    assert turn_input.new_message.parts is not None
    function_call = turn_input.new_message.parts[0].function_call
    assert function_call is not None
    assert function_call.args is not None
    function_call.args[forbidden_key] = "must-not-pass"

    with pytest.raises(ValueError, match=forbidden_key):
        asyncio.run(adapter.collect_events(turn_input))

    assert runner.calls == []


@pytest.mark.parametrize("forbidden_key", FORBIDDEN_BRAND_SEPARATED_OPENMAGI_KEYS)
def test_runner_adapter_rejects_brand_separated_openmagi_keys_mutated_after_input_construction(
    forbidden_key: str,
) -> None:
    runner = FakeRunner()
    adapter = OpenMagiRunnerAdapter(runner=runner)
    turn_input = RunnerTurnInput(
        user_id="user-1",
        session_id="agent:main:app:default",
        turn_id="turn-1",
        invocation_id="turn-1",
        new_message=types.Content(
            role="user",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        name="valid",
                        args={"allowed": "initial"},
                    )
                )
            ],
        ),
        harness_state=build_default_resolved_harness_state(
            agent_role="coding",
            spawn_depth=1,
        ),
    )

    assert turn_input.new_message.parts is not None
    function_call = turn_input.new_message.parts[0].function_call
    assert function_call is not None
    assert function_call.args is not None
    function_call.args[forbidden_key] = "must-not-pass"

    with pytest.raises(ValueError, match=forbidden_key):
        asyncio.run(adapter.collect_events(turn_input))

    assert runner.calls == []


@pytest.mark.parametrize("forbidden_key", FORBIDDEN_UNKNOWN_OPENMAGI_NAMESPACE_KEYS)
def test_runner_adapter_rejects_unknown_openmagi_namespace_keys_mutated_after_input_construction(
    forbidden_key: str,
) -> None:
    runner = FakeRunner()
    adapter = OpenMagiRunnerAdapter(runner=runner)
    turn_input = RunnerTurnInput(
        user_id="user-1",
        session_id="agent:main:app:default",
        turn_id="turn-1",
        invocation_id="turn-1",
        new_message=types.Content(
            role="user",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        name="valid",
                        args={"allowed": "initial"},
                    )
                )
            ],
        ),
        harness_state=build_default_resolved_harness_state(
            agent_role="coding",
            spawn_depth=1,
        ),
    )

    assert turn_input.new_message.parts is not None
    function_call = turn_input.new_message.parts[0].function_call
    assert function_call is not None
    assert function_call.args is not None
    function_call.args[forbidden_key] = "must-not-pass"

    with pytest.raises(ValueError, match="forbidden OpenMagi key"):
        asyncio.run(adapter.collect_events(turn_input))

    assert runner.calls == []


@pytest.mark.parametrize("forbidden_key", FORBIDDEN_COLLAPSED_OPENMAGI_KEYS)
def test_runner_adapter_rejects_collapsed_openmagi_keys_mutated_after_input_construction(
    forbidden_key: str,
) -> None:
    runner = FakeRunner()
    adapter = OpenMagiRunnerAdapter(runner=runner)
    turn_input = RunnerTurnInput(
        user_id="user-1",
        session_id="agent:main:app:default",
        turn_id="turn-1",
        invocation_id="turn-1",
        new_message=types.Content(
            role="user",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        name="valid",
                        args={"allowed": "initial"},
                    )
                )
            ],
        ),
        harness_state=build_default_resolved_harness_state(
            agent_role="coding",
            spawn_depth=1,
        ),
    )

    assert turn_input.new_message.parts is not None
    function_call = turn_input.new_message.parts[0].function_call
    assert function_call is not None
    assert function_call.args is not None
    function_call.args[forbidden_key] = "must-not-pass"

    with pytest.raises(ValueError, match="forbidden OpenMagi key"):
        asyncio.run(adapter.collect_events(turn_input))

    assert runner.calls == []


@pytest.mark.parametrize(
    "openmagi_payload",
    (
        pytest.param("scalar", id="scalar"),
        pytest.param(["list"], id="list"),
        pytest.param({"note": "dict"}, id="dict"),
    ),
)
def test_runner_adapter_rejects_mutated_openmagi_containers_before_runner_call(
    openmagi_payload: object,
) -> None:
    runner = FakeRunner()
    adapter = OpenMagiRunnerAdapter(runner=runner)
    turn_input = RunnerTurnInput(
        user_id="user-1",
        session_id="agent:main:app:default",
        turn_id="turn-1",
        invocation_id="turn-1",
        new_message=types.Content(
            role="user",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        name="valid",
                        args={"allowed": "initial"},
                    )
                )
            ],
        ),
        harness_state=build_default_resolved_harness_state(
            agent_role="coding",
            spawn_depth=1,
        ),
    )

    assert turn_input.new_message.parts is not None
    function_call = turn_input.new_message.parts[0].function_call
    assert function_call is not None
    assert function_call.args is not None
    function_call.args["openmagi"] = openmagi_payload

    with pytest.raises(ValueError, match="openmagi"):
        asyncio.run(adapter.collect_events(turn_input))

    assert runner.calls == []


@pytest.mark.parametrize(
    "payload",
    (
        pytest.param({"openmagi": [{"state": {"turn": "fixture"}}]}, id="state-list"),
        pytest.param({"openmagi": [{"control": {"mode": "fixture"}}]}, id="control-list"),
        pytest.param({"openmagi": [{"evidence": ["fixture"]}]}, id="evidence-list"),
        pytest.param({"openmagi": {"harness-state": {"mode": "fixture"}}}, id="kebab-child"),
    ),
)
def test_runner_adapter_rejects_openmagi_list_containers_before_runner_call(
    payload: dict[str, object],
) -> None:
    runner = FakeRunner()
    adapter = OpenMagiRunnerAdapter(runner=runner)
    turn_input = RunnerTurnInput(
        user_id="user-1",
        session_id="agent:main:app:default",
        turn_id="turn-1",
        invocation_id="turn-1",
        new_message=types.Content(
            role="user",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        name="valid",
                        args={"allowed": "initial"},
                    )
                )
            ],
        ),
        harness_state=build_default_resolved_harness_state(
            agent_role="coding",
            spawn_depth=1,
        ),
    )

    assert turn_input.new_message.parts is not None
    function_call = turn_input.new_message.parts[0].function_call
    assert function_call is not None
    assert function_call.args is not None
    function_call.args.update(payload)

    with pytest.raises(ValueError, match="openmagi"):
        asyncio.run(adapter.collect_events(turn_input))

    assert runner.calls == []


@pytest.mark.parametrize(
    "payload",
    (
        pytest.param({"allowed": "openmagi.currentTurnId=turn-1"}, id="string-assignment"),
        pytest.param({"allowed": ["openmagi.control"]}, id="list-string-identifier"),
        pytest.param(
            {"allowed": {"nested": "openMagiStateDelta"}},
            id="nested-brand-camel-string",
        ),
    ),
)
def test_runner_turn_input_rejects_forbidden_openmagi_identifiers_in_string_values(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="forbidden OpenMagi identifier"):
        RunnerTurnInput(
            user_id="user-1",
            session_id="agent:main:app:default",
            turn_id="turn-1",
            invocation_id="turn-1",
            new_message=types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            name="leak",
                            args=payload,
                        )
                    )
                ],
            ),
            harness_state=build_default_resolved_harness_state(
                agent_role="coding",
                spawn_depth=1,
            ),
        )


def test_runner_adapter_revalidates_mutated_openmagi_identifier_string_values_before_runner_call() -> None:
    runner = FakeRunner()
    adapter = OpenMagiRunnerAdapter(runner=runner)
    turn_input = RunnerTurnInput(
        user_id="user-1",
        session_id="agent:main:app:default",
        turn_id="turn-1",
        invocation_id="turn-1",
        new_message=types.Content(
            role="user",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        name="valid",
                        args={"allowed": "initial"},
                    )
                )
            ],
        ),
        harness_state=build_default_resolved_harness_state(
            agent_role="coding",
            spawn_depth=1,
        ),
    )

    assert turn_input.new_message.parts is not None
    function_call = turn_input.new_message.parts[0].function_call
    assert function_call is not None
    assert function_call.args is not None
    function_call.args["allowed"] = ["openmagi.control"]

    with pytest.raises(ValueError, match="forbidden OpenMagi identifier"):
        asyncio.run(adapter.collect_events(turn_input))

    assert runner.calls == []


@pytest.mark.parametrize(
    "extra_field",
    ("routeAttached", "databaseUrl", "proxyToken"),
)
def test_runner_turn_input_rejects_unexpected_top_level_attachment_fields(
    extra_field: str,
) -> None:
    with pytest.raises(ValidationError, match=extra_field):
        RunnerTurnInput(
            user_id="user-1",
            session_id="agent:main:app:default",
            turn_id="turn-1",
            invocation_id="turn-1",
            new_message=_content(),
            harness_state=build_default_resolved_harness_state(
                agent_role="coding",
                spawn_depth=1,
            ),
            **{extra_field: "must-not-pass"},
        )


# ---------------------------------------------------------------------------
# Streaming RunConfig injection tests
# ---------------------------------------------------------------------------


def _make_turn_input() -> RunnerTurnInput:
    return RunnerTurnInput(
        user_id="user-1",
        session_id="agent:main:app:default",
        turn_id="turn-1",
        invocation_id="turn-1",
        new_message=_content(),
        harness_state=build_default_resolved_harness_state(
            agent_role="coding",
            spawn_depth=1,
        ),
    )


def test_adapter_injects_sse_run_config_by_default() -> None:
    """By default (MAGI_ADK_STREAMING unset) the adapter passes an SSE RunConfig."""
    runner = FakeRunner()
    adapter = OpenMagiRunnerAdapter(runner=runner)

    asyncio.run(adapter.collect_events(_make_turn_input()))

    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert "run_config" in call
    assert isinstance(call["run_config"], RunConfig)
    assert call["run_config"].streaming_mode == StreamingMode.SSE


def test_adapter_omits_run_config_when_streaming_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """MAGI_ADK_STREAMING=0 disables SSE; no run_config is passed to run_async."""
    monkeypatch.setenv("MAGI_ADK_STREAMING", "0")
    runner = FakeRunner()
    adapter = OpenMagiRunnerAdapter(runner=runner)

    asyncio.run(adapter.collect_events(_make_turn_input()))

    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert "run_config" not in call
    assert set(call) == ADK_RUNNER_KWARG_ALLOWLIST


def test_adapter_ignores_caller_run_config_and_uses_only_adapter_owned_sse_config() -> None:
    """The caller-supplied run_config (openmagi dict) must NOT reach run_async.

    Only the adapter-owned SSE RunConfig instance is allowed — not the caller's dict.
    This verifies the side-channel block is preserved even with streaming enabled.
    """
    runner = FakeRunner()
    adapter = OpenMagiRunnerAdapter(runner=runner)
    turn_input = RunnerTurnInput(
        user_id="user-1",
        session_id="agent:main:app:default",
        turn_id="turn-1",
        invocation_id="turn-1",
        new_message=_content(),
        # Caller tries to pass a run_config with openmagi control payload.
        run_config={"openmagi": {"control": "local-only"}, "temperature": 0.5},
        harness_state=build_default_resolved_harness_state(
            agent_role="coding",
            spawn_depth=1,
        ),
    )

    asyncio.run(adapter.collect_events(turn_input))

    assert len(runner.calls) == 1
    call = runner.calls[0]
    # The adapter-owned RunConfig must be present (streaming default-on).
    assert "run_config" in call
    assert isinstance(call["run_config"], RunConfig)
    assert call["run_config"].streaming_mode == StreamingMode.SSE
    # The caller's dict payload must NOT appear anywhere in the call.
    flattened = _flatten_values(call)
    assert "local-only" not in flattened
    assert "temperature" not in flattened
    assert 0.5 not in flattened
