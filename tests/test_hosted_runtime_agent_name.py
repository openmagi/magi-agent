"""Governed hosted path: the ADK Agent name must be a valid identifier.

B6 (identity parity) set the governed app_name to the legacy hyphenated string
``openmagi-gate5b4c3-shadow-generation`` so durable session rows are keyed
identically across the flag flip. But the Runner's ``app_name`` (which may
contain hyphens) is NOT a valid ADK ``Agent.name`` (ADK enforces
``name.isidentifier()``). ``build_hosted_runtime`` must therefore derive an
identifier-safe Agent name from the app_name; otherwise the real governed path
raises at Agent construction (the fake primitives in other tests mask this).
"""

from __future__ import annotations

import pytest

from magi_agent.runtime.hosted_runtime import (
    GATE5B_SHADOW_APP_NAME,
    _agent_identifier,
    build_hosted_runtime,
)

from tests.support.engine_fakes import MockRunner
from tests.support.gate5b4c3_fakes import (
    _FakeAgent,
    _FakeGenerateContentConfig,
    make_primitives,
)


def _loader(runner: object) -> object:
    primitives = make_primitives(runner)

    def _load() -> object:
        return primitives

    return _load


def test_agent_identifier_sanitizes_hyphens() -> None:
    got = _agent_identifier("openmagi-gate5b4c3-shadow-generation")
    assert got.isidentifier()
    assert got == "openmagi_gate5b4c3_shadow_generation"


def test_agent_identifier_handles_leading_digit_and_empty() -> None:
    assert _agent_identifier("9x").isidentifier()
    assert _agent_identifier("").isidentifier()


def test_build_hosted_runtime_agent_name_is_valid_identifier() -> None:
    _FakeAgent.created_kwargs = {}
    build_hosted_runtime(
        adk_primitives_loader=_loader(MockRunner([])),
        instruction="sys",
        model="fake-model",
        adk_tools=(),
        app_name=GATE5B_SHADOW_APP_NAME,
        generate_content_config=_FakeGenerateContentConfig(),
    )
    name = _FakeAgent.created_kwargs.get("name")
    assert isinstance(name, str) and name.isidentifier(), name


def test_real_adk_agent_rejects_hyphenated_accepts_sanitized() -> None:
    # Proves the fix is necessary AND sufficient against the real library.
    adk_agents = pytest.importorskip("google.adk.agents")
    Agent = adk_agents.Agent
    with pytest.raises(Exception):
        Agent(name=GATE5B_SHADOW_APP_NAME, model="fake")
    # The sanitized name is accepted (no raise).
    Agent(name=_agent_identifier(GATE5B_SHADOW_APP_NAME), model="fake")
