"""Scripted fake LLM for the authoring harness (zero network).

``ScriptedLlm`` replays a FIFO sequence of canned envelope strings — one per
turn — through the exact ADK model contract that
``magi_agent.customize.shacl_compiler._invoke_llm`` consumes: a ``model`` object
exposing an async-generator ``generate_content_async(request, stream=False)``
whose yielded responses carry ``resp.content.parts[*].text``.

It records every request in ``capture_log`` so golden scenarios can assert what
the engine SENT to the compiler (fence nonce present, operator answers already
reflected in the prompt), and raises ``ScriptExhaustedError`` when the script
and the turn count diverge, so a scenario whose ``llm_script`` is the wrong
length fails loudly instead of silently reusing the last envelope.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from typing import Any, Callable


class ScriptExhaustedError(RuntimeError):
    """The engine asked for one more envelope than the script provides."""


@dataclass
class PromptCapture:
    """One recorded call into the fake model."""

    system_instruction: str
    #: The final (current-user) prompt text passed to ``_invoke_llm``.
    prompt: str
    #: Every turn's text in order (prior turns first, current prompt last),
    #: flattened from the ``LlmRequest.contents`` the engine built.
    contents: list[str]


@dataclass
class _FakePart:
    text: str


@dataclass
class _FakeContent:
    parts: list[_FakePart]


@dataclass
class _FakeResponse:
    content: _FakeContent


class ScriptedLlm:
    """FIFO replay of canned compiler envelopes with prompt capture.

    A single ``ScriptedLlm`` instance is shared across all turns of one
    scenario; each ``_invoke_llm`` drive pops the next scripted envelope. Use
    :meth:`as_factory` to obtain the ``() -> model`` factory the engines call.
    """

    def __init__(self, script: Sequence[str]) -> None:
        self._script: list[str] = list(script)
        self._cursor = 0
        self.capture_log: list[PromptCapture] = []

    @property
    def remaining(self) -> int:
        return len(self._script) - self._cursor

    def _next_text(self) -> str:
        if self._cursor >= len(self._script):
            raise ScriptExhaustedError(
                f"scripted LLM exhausted: {len(self._script)} envelope(s) "
                f"scripted but the engine requested one more"
            )
        text = self._script[self._cursor]
        self._cursor += 1
        return text

    def _record(self, llm_request: Any) -> None:
        system_instruction = ""
        contents: list[str] = []
        config = getattr(llm_request, "config", None)
        if config is not None:
            system_instruction = getattr(config, "system_instruction", "") or ""
        for content in getattr(llm_request, "contents", []) or []:
            parts = getattr(content, "parts", []) or []
            text = "".join(getattr(p, "text", "") or "" for p in parts)
            contents.append(text)
        prompt = contents[-1] if contents else ""
        self.capture_log.append(
            PromptCapture(
                system_instruction=system_instruction,
                prompt=prompt,
                contents=contents,
            )
        )

    async def generate_content_async(
        self, llm_request: Any, stream: bool = False
    ) -> AsyncGenerator[_FakeResponse, None]:
        self._record(llm_request)
        text = self._next_text()
        yield _FakeResponse(content=_FakeContent(parts=[_FakePart(text=text)]))

    # The engines resolve a model via ``model_factory()`` then read
    # ``model.model`` for logging; expose a stable id.
    model = "scripted-fake"

    def as_factory(self) -> Callable[[], "ScriptedLlm"]:
        """Return a ``() -> model`` factory yielding THIS scripted instance.

        Sharing one instance across turns is what makes the FIFO cursor and the
        capture log span the whole scenario.
        """
        return lambda: self
