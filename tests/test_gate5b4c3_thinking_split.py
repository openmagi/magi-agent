"""gate5b4c3 (hosted live chat) must split model thinking from the answer.

`_text_chunks_from_parts` previously collected text from ALL parts including
`thought=True`, so model reasoning was concatenated into the visible answer and
no thinking channel was emitted. Visible text must exclude thought parts, and a
parallel helper must collect thought text for a thinking_delta stream.
"""

from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    _text_chunks_from_parts,
    _thinking_chunks_from_parts,
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
