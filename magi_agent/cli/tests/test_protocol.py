from __future__ import annotations

from magi_agent.cli.protocol import (
    AssistantFrame,
    ControlCancel,
    ControlRequestFrame,
    ControlResponse,
    ResultFrame,
    StreamEvent,
    SystemInit,
    SystemStatus,
    UserFrame,
    UserInput,
)


def test_system_init_discriminators_and_defaults() -> None:
    frame = SystemInit(model="m", tools=["a", "b"], cwd="/tmp")
    assert frame.type == "system"
    assert frame.subtype == "init"
    assert frame.tools == ["a", "b"]
    assert frame.uuid  # auto-generated
    assert frame.session_id == ""


def test_assistant_frame() -> None:
    frame = AssistantFrame(message={"role": "assistant", "content": "hi"})
    assert frame.type == "assistant"
    assert frame.parent_tool_use_id is None


def test_user_frame() -> None:
    frame = UserFrame(message={"role": "user"})
    assert frame.type == "user"


def test_stream_event() -> None:
    frame = StreamEvent(event={"type": "text_delta", "delta": "x"})
    assert frame.type == "stream_event"
    assert frame.event["delta"] == "x"


def test_system_status_subtypes() -> None:
    for subtype in ("status", "task_started", "task_progress", "compact_boundary"):
        frame = SystemStatus(subtype=subtype)  # type: ignore[arg-type]
        assert frame.type == "system"
        assert frame.subtype == subtype


def test_result_frame() -> None:
    frame = ResultFrame(
        subtype="success",
        result="done",
        usage={"input_tokens": 1},
        total_cost_usd=0.5,
        is_error=False,
    )
    assert frame.type == "result"
    assert frame.subtype == "success"
    assert frame.result == "done"
    assert frame.is_error is False


def test_result_frame_error_subtypes() -> None:
    for subtype in ("error_max_turns", "error_during_execution"):
        frame = ResultFrame(subtype=subtype, is_error=True)  # type: ignore[arg-type]
        assert frame.subtype == subtype
        assert frame.is_error is True


def test_control_request_frame() -> None:
    frame = ControlRequestFrame(request_id="req-1", request={"tool": "Bash"})
    assert frame.type == "control_request"
    assert frame.request_id == "req-1"


def test_inbound_user_input() -> None:
    frame = UserInput(message={"role": "user", "content": "hi"})
    assert frame.type == "user"


def test_inbound_control_response() -> None:
    frame = ControlResponse(request_id="req-1", response={"decision": "allow"})
    assert frame.type == "control_response"
    assert frame.request_id == "req-1"


def test_inbound_control_cancel() -> None:
    frame = ControlCancel(request_id="req-1")
    assert frame.type == "control_cancel_request"


def test_round_trip_model_dump_validate() -> None:
    frames = [
        SystemInit(model="m"),
        AssistantFrame(message={"content": "x"}),
        UserFrame(message={"role": "user"}),
        StreamEvent(event={"a": 1}),
        SystemStatus(subtype="task_started"),
        ResultFrame(subtype="success", result="ok"),
        ControlRequestFrame(request_id="r"),
    ]
    for frame in frames:
        dumped = frame.model_dump(mode="json")
        restored = type(frame).model_validate(dumped)
        assert restored == frame
        assert restored.type == frame.type
