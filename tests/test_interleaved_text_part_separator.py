"""Regression: answer-text blocks resumed after a tool burst must be paragraph
separated so downstream separator-free concatenation does not glue them.

The model emits distinct answer-text blocks around tool calls with no
separating newline (each block reads like a fresh message). Every downstream
sink concatenates the ``text_delta`` payloads with "" (the local turn-store
reducer ``self._content += delta`` and the frontend segment/stream reducers),
so a heading or bold opening a post-tool block glued onto the tail of the
pre-tool narration and rendered inline as literal text ("...필요합니다.## 결과").
The fix inserts a minimal paragraph break on the FIRST text_delta emitted after
a tool, at the single delta source (event_adapter), so every consumer heals.

These tests concatenate the emitted ``text_delta`` deltas across a turn exactly
as the store reducer / frontend do, and assert the boundary. They also lock the
non-goal: consecutive fragments of the SAME block (no tool between them) are
never touched.
"""

from google.adk.events import Event
from google.genai import types

from magi_agent.adk_bridge.event_adapter import OpenMagiEventBridge


def _text_event(text: str, *, partial: bool = True) -> Event:
    return Event(
        author="model",
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        partial=partial,
        invocation_id="turn-1",
    )


def _tool_call_event() -> Event:
    return Event(
        id="event-tool",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="tool-1", name="Bash", args={"command": "ls"}
                    )
                )
            ],
        ),
        invocation_id="turn-1",
    )


def _tool_response_event() -> Event:
    return Event(
        id="event-tool-resp",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="tool-1", name="Bash", response={"output": "a.txt"}
                    )
                )
            ],
        ),
        invocation_id="turn-1",
    )


def _joined_text_deltas(events: list[Event]) -> str:
    """Project a whole turn and concatenate its text_delta deltas the same way
    the store reducer and the frontend segment reducer do (with "")."""
    bridge = OpenMagiEventBridge()
    parts: list[str] = []
    for event in events:
        projection = bridge.project_adk_event(event, turn_id="turn-1")
        for agent_event in projection.agent_events:
            if agent_event.get("type") == "text_delta":
                parts.append(str(agent_event["delta"]))
    return "".join(parts)


def test_heading_after_tool_gets_paragraph_break() -> None:
    # The exact reported symptom: "## 결과" opened a post-tool block and glued
    # onto the pre-tool narration, rendering inline instead of as a heading.
    result = _joined_text_deltas(
        [
            _text_event("확인이 필요합니다."),
            _tool_call_event(),
            _tool_response_event(),
            _text_event("## 결과"),
        ]
    )
    assert result == "확인이 필요합니다.\n\n## 결과"


def test_consecutive_fragments_without_tool_are_never_separated() -> None:
    # Highest-risk regression: two streaming fragments of ONE block (no tool
    # between them) must concatenate verbatim, with no injected separator.
    result = _joined_text_deltas(
        [
            _text_event("데이터를 확보"),
            _text_event("하겠습니다."),
        ]
    )
    assert result == "데이터를 확보하겠습니다."


def test_existing_double_newline_is_not_doubled() -> None:
    result = _joined_text_deltas(
        [
            _text_event("작업했습니다.\n\n"),
            _tool_call_event(),
            _text_event("## 결과"),
        ]
    )
    assert result == "작업했습니다.\n\n## 결과"


def test_single_trailing_newline_is_bumped_to_paragraph_break() -> None:
    result = _joined_text_deltas(
        [
            _text_event("작업했습니다.\n"),
            _tool_call_event(),
            _text_event("다음 단계"),
        ]
    )
    assert result == "작업했습니다.\n\n다음 단계"


def test_incoming_leading_newline_is_completed_not_doubled() -> None:
    result = _joined_text_deltas(
        [
            _text_event("작업했습니다."),
            _tool_call_event(),
            _text_event("\n## 결과"),
        ]
    )
    assert result == "작업했습니다.\n\n## 결과"


def test_first_text_after_tools_only_prefix_has_no_leading_separator() -> None:
    # A tool ran before any answer text: the very first text_delta of the turn
    # must not be prefixed with a stray paragraph break.
    result = _joined_text_deltas(
        [
            _tool_call_event(),
            _tool_response_event(),
            _text_event("첫 답변입니다."),
        ]
    )
    assert result == "첫 답변입니다."


def test_only_the_first_post_tool_fragment_is_separated() -> None:
    # After the tool, the first fragment opens the paragraph; later fragments of
    # the same block must not each get a break.
    result = _joined_text_deltas(
        [
            _text_event("앞 문장."),
            _tool_call_event(),
            _text_event("**결과**"),
            _text_event(" 이어서"),
        ]
    )
    assert result == "앞 문장.\n\n**결과** 이어서"
