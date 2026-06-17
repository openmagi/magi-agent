"""Shared fakes for driving MagiEngineDriver in tests (golden + unit).

Provides:
- ``MockRunner(events)`` — async-generator yielding real ``google.adk.events.Event``
- ``text_event(text, *, partial, turn_complete)`` — model text event
- ``call_event(tool, args, call_id)`` — function-call event
- ``response_event(tool, payload, call_id)`` — function-response event

These builders are ported verbatim from the inline helpers in
``magi_agent/cli/tests/test_engine.py`` so that events are byte-identical
to those used by the existing engine test suite.
"""
from __future__ import annotations

from google.adk.events import Event
from google.genai import types


class MockRunner:
    """Yields a fixed list of ADK events. Matches the ``run_async`` signature
    the OpenMagiRunnerAdapter calls."""

    def __init__(self, events: list[Event]) -> None:
        self._events = events

    async def run_async(self, **_kwargs: object):
        for event in self._events:
            yield event


def text_event(
    text: str,
    *,
    partial: bool = True,
    turn_complete: bool = False,
) -> Event:
    return Event(
        author="model",
        partial=partial,
        turn_complete=turn_complete,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
    )


def call_event(tool: str, args: dict, call_id: str) -> Event:
    return Event(
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(name=tool, args=args, id=call_id)
                )
            ],
        ),
    )


def response_event(tool: str, payload: dict, call_id: str) -> Event:
    return Event(
        author="user",
        content=types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name=tool, response=payload, id=call_id
                    )
                )
            ],
        ),
    )
