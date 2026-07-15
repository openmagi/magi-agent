"""Hosted live chat must split model thinking from the answer.

Visible answer text previously collected text from ALL parts including
`thought=True`, so model reasoning was concatenated into the visible answer and
no thinking channel was emitted. Visible text must exclude thought parts, and a
parallel helper must collect thought text for a thinking_delta stream.

P5-M1b: the split helpers were promoted from the retired gate5b4c3 live runner
boundary into the shared ``runtime.public_events`` home (byte-identical), which
is where the governed path's wire-shape invariants live alongside
``result_digest`` / ``tool_event_id``.
"""

from magi_agent.runtime.public_events import (
    text_chunks_from_parts as _text_chunks_from_parts,
    thinking_chunks_from_parts as _thinking_chunks_from_parts,
)


class _Part:
    def __init__(self, text=None, thought=False):
        self.text = text
        self.thought = thought


def test_visible_text_excludes_thought_parts() -> None:
    parts = [_Part("The answer is 42."), _Part("internal reasoning", thought=True)]
    assert _text_chunks_from_parts(parts) == ["The answer is 42."]


def test_thinking_chunks_collect_only_thought_parts() -> None:
    parts = [
        _Part("visible answer"),
        _Part("reasoning a", thought=True),
        _Part("reasoning b", thought=True),
    ]
    assert _thinking_chunks_from_parts(parts) == ["reasoning a", "reasoning b"]


def test_thinking_chunks_empty_when_no_thought() -> None:
    parts = [_Part("just an answer")]
    assert _thinking_chunks_from_parts(parts) == []
