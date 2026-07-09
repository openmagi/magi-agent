"""The interactive rule compiler's LIVE LLM path must be reachable.

Regression pin for a template bug found by the authoring QA harness: the
system-instruction template contained UNESCAPED literal JSON braces, so
``str.format`` raised KeyError on EVERY call. The llm-unavailable except
swallowed it, and every turn silently fell back to the deterministic flow -
the live compiler path was dead while all existing tests passed (they only
ever ran with ``model_factory=None``).
"""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator

import pytest

from magi_agent.customize.nl_compiler_interactive import (
    _INTERACTIVE_SYSTEM_INSTRUCTION_TMPL,
    step_compile,
)
from magi_agent.customize.rule_compiler import _KIND_MENU


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeLlmResponse:
    def __init__(self, text: str) -> None:
        self.content = _FakeContent(text)


def _scripted_llm(json_text: str, captured: list[Any]):
    class _FakeLlm:
        model = "fake-compiler-model"

        async def generate_content_async(
            self, llm_request: Any, stream: bool = False
        ) -> AsyncGenerator:
            captured.append(llm_request)
            yield _FakeLlmResponse(json_text)

    return _FakeLlm()


def test_interactive_template_renders_without_keyerror() -> None:
    rendered = _INTERACTIVE_SYSTEM_INSTRUCTION_TMPL.format(
        kind_menu=_KIND_MENU, nonce="abc123"
    )
    # Real placeholders substituted...
    assert "UNTRUSTED-abc123" in rendered
    assert "{kind_menu}" not in rendered
    # ...and the literal JSON example survived as single braces.
    assert '{\n    "assistant_message"' in rendered


@pytest.mark.asyncio
async def test_step_compile_reaches_live_llm_path() -> None:
    captured: list[Any] = []
    envelope = json.dumps(
        {
            "assistant_message": "Got it - blocking risky shell commands.",
            "draft_updates": {"scope": "coding"},
            "questions": [],
        }
    )
    factory_calls: list[bool] = []

    def factory() -> Any:
        factory_calls.append(True)
        return _scripted_llm(envelope, captured)

    result = await step_compile(
        history=[{"role": "user", "content": "block risky shell commands"}],
        draft_so_far=None,
        answers=None,
        model_factory=factory,
    )

    # The factory MUST be reached (on the broken template, .format raised
    # BEFORE model_factory() and the except swallowed it -> factory_calls==[]).
    assert factory_calls, "live LLM path never reached the model factory"
    assert captured, "model was never invoked"
    # The scripted envelope actually landed on the turn.
    assert result["assistant_message"] == "Got it - blocking risky shell commands."
