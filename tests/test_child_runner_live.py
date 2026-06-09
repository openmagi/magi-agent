from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from magi_agent.runtime.child_runner_boundary import (
    ChildRunnerConfig,
    ChildTaskRequest,
    LocalChildRunnerBoundary,
)
from magi_agent.runtime.child_runner_live import (
    LIVE_CHILD_RUNNER_ENABLED_ENV,
    LIVE_CHILD_RUNNER_KILL_SWITCH_ENV,
    RealLocalChildRunner,
    is_live_child_runner_enabled,
)

_PROVIDER_ENV = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "FIREWORKS_API_KEY",
    "MAGI_PROVIDER",
    "MAGI_MODEL",
)


@pytest.fixture(autouse=True)
def _isolate_provider_env(monkeypatch, tmp_path) -> None:
    """No real key / config can influence these tests."""
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(LIVE_CHILD_RUNNER_ENABLED_ENV, raising=False)
    monkeypatch.delenv(LIVE_CHILD_RUNNER_KILL_SWITCH_ENV, raising=False)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))


def _request(**overrides: object) -> ChildTaskRequest:
    data: dict[str, object] = {
        "parentExecutionId": "parent-exec-1",
        "turnId": "turn-1",
        "taskId": "task-1",
        "objective": "Summarise the delegated subtask without leaking raw logs.",
        "role": "research",
        "delivery": "return",
    }
    data.update(overrides)
    return ChildTaskRequest(**data)


# --------------------------------------------------------------------------- #
# Fakes (NO network): a fully-injected runner yielding canned events.          #
# --------------------------------------------------------------------------- #


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeEvent:
    def __init__(self, text: str) -> None:
        self.content = _FakeContent(text)


class _FakeRunner:
    """Mimics ``CliModelRunner.run_async`` — yields canned text events."""

    def __init__(self, text: str = "ANSWER: 42") -> None:
        self._text = text
        self.calls = 0

    async def run_async(self, **kwargs: object) -> AsyncGenerator[object, None]:
        self.calls += 1
        yield _FakeEvent(self._text)


class _RaisingRunner:
    def __init__(self) -> None:
        self.calls = 0

    async def run_async(self, **kwargs: object) -> AsyncGenerator[object, None]:
        self.calls += 1
        raise RuntimeError("boom /Users/kevin/secret sk-live-AAA")
        yield  # pragma: no cover - unreachable, makes this an async generator.


def _provider_config(api_key: str = "sk-test") -> object:
    from magi_agent.cli.providers import ProviderConfig

    return ProviderConfig(
        provider="anthropic", model="claude-sonnet-4-6", api_key=api_key
    )


# --------------------------------------------------------------------------- #
# Env gate                                                                     #
# --------------------------------------------------------------------------- #


def test_is_live_child_runner_enabled_off_by_default() -> None:
    assert is_live_child_runner_enabled(env={}) is False


def test_is_live_child_runner_enabled_on_when_enabled_no_kill_switch() -> None:
    assert (
        is_live_child_runner_enabled(env={LIVE_CHILD_RUNNER_ENABLED_ENV: "1"})
        is True
    )
    assert (
        is_live_child_runner_enabled(env={LIVE_CHILD_RUNNER_ENABLED_ENV: "TRUE"})
        is True
    )


def test_is_live_child_runner_enabled_off_when_kill_switch_set() -> None:
    assert (
        is_live_child_runner_enabled(
            env={
                LIVE_CHILD_RUNNER_ENABLED_ENV: "1",
                LIVE_CHILD_RUNNER_KILL_SWITCH_ENV: "1",
            }
        )
        is False
    )


def test_is_live_child_runner_enabled_ignores_garbage_values() -> None:
    assert (
        is_live_child_runner_enabled(env={LIVE_CHILD_RUNNER_ENABLED_ENV: "maybe"})
        is False
    )


# --------------------------------------------------------------------------- #
# run_child happy path (fake-injected runner — no network)                     #
# --------------------------------------------------------------------------- #


def test_run_child_completes_with_final_text_from_injected_runner() -> None:
    fake = _FakeRunner(text="ANSWER: the delegated subtask is done")
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        runner=fake,
    )

    output = asyncio.run(runner.run_child(_request()))

    assert fake.calls == 1
    assert output["status"] == "completed"
    assert "the delegated subtask is done" in str(output["summary"])
    assert set(output.keys()) == {
        "childExecutionId",
        "status",
        "summary",
        "evidenceRefs",
        "artifactRefs",
        "auditEventRefs",
    }
    assert output["evidenceRefs"] == ()
    assert output["artifactRefs"] == ()
    assert output["auditEventRefs"] == ()
    assert str(output["childExecutionId"]).startswith("child-exec-")


def test_run_child_uses_model_factory_seam_with_build_cli_model_runner() -> None:
    """When NO runner is injected, the ``model_factory`` seam is forwarded to
    ``build_cli_model_runner`` so a fake ``BaseLlm`` drives the turn (no network)."""
    from google.adk.models import BaseLlm, LlmResponse
    from google.genai import types

    class _FakeEchoLlm(BaseLlm):
        async def generate_content_async(
            self, llm_request: object, stream: bool = False
        ) -> AsyncGenerator[LlmResponse, None]:
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text="ECHO: child turn ran")],
                )
            )

    def _factory(config: object) -> BaseLlm:
        return _FakeEchoLlm(model="fake")

    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        model_factory=_factory,
    )

    output = asyncio.run(runner.run_child(_request()))

    assert output["status"] == "completed"
    assert "ECHO: child turn ran" in str(output["summary"])


# --------------------------------------------------------------------------- #
# End-to-end through Task A's boundary                                         #
# --------------------------------------------------------------------------- #


def test_boundary_drives_real_runner_when_live_gate_enabled() -> None:
    fake = _FakeRunner(text="ANSWER: boundary path complete")
    real = RealLocalChildRunner(provider_config=_provider_config(), runner=fake)
    boundary = LocalChildRunnerBoundary(
        ChildRunnerConfig(enabled=True, liveChildRunnerEnabled=True),
        child_runner=real,
    )

    result = asyncio.run(boundary.run(_request()))

    assert fake.calls == 1
    assert result.status == "ok"
    assert result.envelope is not None
    assert result.envelope.status == "completed"
    assert result.envelope.child_ref.startswith("child:")
    assert "boundary path complete" in result.envelope.summary
    projection = result.public_projection()
    assert projection["diagnosticMetadata"]["liveChildRunnerCalled"] is True
    for flag_value in projection["authorityFlags"].values():
        assert flag_value is False


def test_boundary_blocks_real_runner_when_live_gate_disabled() -> None:
    fake = _FakeRunner()
    real = RealLocalChildRunner(provider_config=_provider_config(), runner=fake)
    boundary = LocalChildRunnerBoundary(
        ChildRunnerConfig(enabled=True, localFakeChildRunnerEnabled=True),
        child_runner=real,
    )

    result = asyncio.run(boundary.run(_request()))

    assert fake.calls == 0
    assert result.status == "blocked"
    assert result.error_code == "live_child_runner_not_enabled"


# --------------------------------------------------------------------------- #
# Degrade paths (never raise)                                                  #
# --------------------------------------------------------------------------- #


def test_run_child_blocks_when_no_provider_key(monkeypatch) -> None:
    """No injected config + no env/config key → blocked, never executes."""
    fake = _FakeRunner()
    runner = RealLocalChildRunner(runner=fake)  # no provider_config, no key

    output = asyncio.run(runner.run_child(_request()))

    assert output["status"] == "blocked"
    assert output["summary"] == "child_provider_key_missing"
    assert fake.calls == 0


def test_run_child_blocks_on_unknown_model_tier() -> None:
    fake = _FakeRunner()
    runner = RealLocalChildRunner(provider_config=_provider_config(), runner=fake)

    # A model not in ModelTierRegistry.with_defaults() → blocked.
    output = asyncio.run(
        runner.run_child(_request(provider="anthropic", model="totally-made-up-9000"))
    )

    assert output["status"] == "blocked"
    assert output["summary"] == "child_model_route_unknown"
    assert fake.calls == 0


def test_run_child_never_raises_when_runner_raises_mid_turn() -> None:
    raising = _RaisingRunner()
    runner = RealLocalChildRunner(provider_config=_provider_config(), runner=raising)

    output = asyncio.run(runner.run_child(_request()))

    assert raising.calls == 1
    assert output["status"] in {"failed", "blocked"}
    assert output["summary"] == "child_turn_error"
    # No raw error text (path / token) leaks into the output.
    encoded = repr(output)
    assert "/Users/kevin/secret" not in encoded
    assert "sk-live-AAA" not in encoded


def test_boundary_sanitises_blocked_output_from_real_runner() -> None:
    """A degraded (blocked) mapping still routes through the boundary cleanly."""
    runner = RealLocalChildRunner(runner=_FakeRunner())  # no key → blocked
    boundary = LocalChildRunnerBoundary(
        ChildRunnerConfig(enabled=True, liveChildRunnerEnabled=True),
        child_runner=runner,
    )

    result = asyncio.run(boundary.run(_request()))

    # The boundary's _envelope_from_output coerces unknown status to completed
    # but only "completed|blocked|failed" pass through; "blocked" is preserved.
    assert result.status == "ok"
    assert result.envelope is not None
    assert result.envelope.status == "blocked"


# --------------------------------------------------------------------------- #
# Marker contract                                                             #
# --------------------------------------------------------------------------- #


def test_real_local_child_runner_declares_live_provider_marker() -> None:
    assert RealLocalChildRunner.openmagi_live_provider is True
    assert RealLocalChildRunner(runner=_FakeRunner()).openmagi_live_provider is True


# --------------------------------------------------------------------------- #
# Import-boundary: importing the module must not pull heavy runtime in.        #
# --------------------------------------------------------------------------- #


def test_child_runner_live_import_stays_light() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.runtime.child_runner_live")

forbidden_prefixes = (
    "litellm",
    "google.adk",
    "google.adk.runners",
    "google.adk.models.lite_llm",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"child_runner_live import loaded forbidden modules: {loaded}")
""",
        ],
        check=False,
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
