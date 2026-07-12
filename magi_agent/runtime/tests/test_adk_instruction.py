"""State-injection-safe instruction wrapper (live incident 2026-07-12).

A SKILL.md body activated onto the system instruction carried SEC EDGAR URL
templates ({CIK}/{ACC}); ADK's inject_session_state treated them as session
state placeholders and killed every turn with KeyError before the model ran.
The wrapper is ADK's documented InstructionProvider bypass.
"""
from __future__ import annotations

import asyncio

import pytest

from magi_agent.runtime.adk_instruction import (
    StateInjectionSafeInstruction,
    state_injection_safe_instruction,
)

_BRACED = "Fetch https://sec.gov/cgi-bin/browse-edgar?CIK={CIK}&type={T} then read {file}."


def test_wrapper_is_callable_returning_exact_text() -> None:
    wrapped = state_injection_safe_instruction(_BRACED)
    assert wrapped(None) == _BRACED
    assert wrapped() == _BRACED


def test_wrapper_str_is_the_raw_text_but_not_a_str_instance() -> None:
    wrapped = state_injection_safe_instruction(_BRACED)
    assert str(wrapped) == _BRACED
    # NOT a str subclass: ADK routes isinstance(_, str) instructions through
    # session-state injection, which is the exact path being bypassed.
    assert not isinstance(wrapped, str)
    assert wrapped == _BRACED  # str-comparable for assertions/diagnostics


def test_real_adk_bypasses_state_injection_for_the_wrapper() -> None:
    """The decisive check against the installed google-adk: a plain-str
    instruction with a brace token raises KeyError in inject_session_state,
    while the wrapped instruction reaches canonical_instruction with
    bypass_state_injection=True and byte-identical text."""
    adk_agents = pytest.importorskip("google.adk.agents")

    agent = adk_agents.LlmAgent(
        name="probe", instruction=state_injection_safe_instruction(_BRACED)
    )

    class _Ctx:  # minimal ReadonlyContext stand-in for canonical_instruction
        pass

    text, bypass = asyncio.run(agent.canonical_instruction(_Ctx()))
    assert bypass is True
    assert text == _BRACED


def test_real_adk_str_instruction_with_braces_raises() -> None:
    """Pin the failure mode this wrapper exists for: if this stops raising on
    an ADK upgrade, the wrapper can be retired."""
    instructions_utils = pytest.importorskip("google.adk.utils.instructions_utils")

    class _State(dict):
        pass

    class _Session:
        state = _State()

    class _InvocationContext:
        session = _Session()
        artifact_service = None

    class _Ctx:
        _invocation_context = _InvocationContext()

    with pytest.raises(KeyError):
        asyncio.run(instructions_utils.inject_session_state("see {CIK}", _Ctx()))


def test_repr_does_not_leak_the_text() -> None:
    wrapped = StateInjectionSafeInstruction("secret-ish body")
    assert "secret-ish" not in repr(wrapped)
