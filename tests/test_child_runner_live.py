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
    _DEFAULT_CHILD_TURN_TIMEOUT_S,
    _DEGRADE_KEY_MISSING,
    _DEGRADE_ROUTE_UNKNOWN,
    _DEGRADE_TIMEOUT,
    _DEGRADE_TURN_ERROR,
    _MAX_TURN_TIMEOUT_S,
    RealLocalChildRunner,
    derive_child_session_id,
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
    "MAGI_SUBAGENT_GOVERNED_TURN_ENABLED",
    "MAGI_CHILD_MEMORY_INHERIT_ENABLED",
    "MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED",
)


@pytest.fixture(autouse=True)
def _isolate_provider_env(monkeypatch, tmp_path) -> None:
    """No real key / config can influence these tests."""
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(LIVE_CHILD_RUNNER_ENABLED_ENV, raising=False)
    monkeypatch.delenv(LIVE_CHILD_RUNNER_KILL_SWITCH_ENV, raising=False)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))


_GOVERNED_OFF_ENV = {"MAGI_SUBAGENT_GOVERNED_TURN_ENABLED": "0"}


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

    return ProviderConfig(provider="anthropic", model="claude-sonnet-4-6", api_key=api_key)


class _SlowRunner:
    """Mimics ``run_async`` but sleeps before yielding — to trip a turn budget."""

    def __init__(self, sleep_s: float = 5.0, text: str = "ANSWER: late") -> None:
        self._sleep_s = sleep_s
        self._text = text
        self.calls = 0

    async def run_async(self, **kwargs: object) -> AsyncGenerator[object, None]:
        self.calls += 1
        await asyncio.sleep(self._sleep_s)
        yield _FakeEvent(self._text)


class _RecordingRunner:
    """Captures the kwargs ``build_cli_model_runner`` was called with."""

    def __init__(self, text: str = "ANSWER: captured") -> None:
        self._text = text
        self.calls = 0

    async def run_async(self, **kwargs: object) -> AsyncGenerator[object, None]:
        self.calls += 1
        yield _FakeEvent(self._text)


# --------------------------------------------------------------------------- #
# Env gate                                                                     #
# --------------------------------------------------------------------------- #


def test_is_live_child_runner_enabled_off_by_default() -> None:
    assert is_live_child_runner_enabled(env={}) is False


def test_is_live_child_runner_enabled_on_when_enabled_no_kill_switch() -> None:
    assert is_live_child_runner_enabled(env={LIVE_CHILD_RUNNER_ENABLED_ENV: "1"}) is True
    assert is_live_child_runner_enabled(env={LIVE_CHILD_RUNNER_ENABLED_ENV: "TRUE"}) is True


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
    assert is_live_child_runner_enabled(env={LIVE_CHILD_RUNNER_ENABLED_ENV: "maybe"}) is False


# --------------------------------------------------------------------------- #
# run_child happy path (fake-injected runner — no network)                     #
# --------------------------------------------------------------------------- #


def test_run_child_completes_with_final_text_from_injected_runner() -> None:
    fake = _FakeRunner(text="ANSWER: the delegated subtask is done")
    runner = RealLocalChildRunner(
        env=_GOVERNED_OFF_ENV,
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


def test_run_child_emits_progress_for_streamed_child_chunks() -> None:
    class _ChunkRunner:
        async def run_async(self, **kwargs: object) -> AsyncGenerator[object, None]:
            yield _FakeEvent("first child chunk")
            yield _FakeEvent("second child chunk")

    progress_events: list[dict[str, object]] = []
    runner = RealLocalChildRunner(
        env=_GOVERNED_OFF_ENV,
        provider_config=_provider_config(),
        runner=_ChunkRunner(),
        progress_sink=lambda event: progress_events.append(dict(event)),
    )

    output = asyncio.run(runner.run_child(_request()))

    assert output["status"] == "completed"
    assert [event["type"] for event in progress_events] == [
        "child_progress",
        "child_progress",
    ]
    assert [event["detail"] for event in progress_events] == [
        "Child model streamed output chunk (17 chars)",
        "Child model streamed output chunk (18 chars)",
    ]
    assert "first child chunk" not in repr(progress_events)
    assert "second child chunk" not in repr(progress_events)


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
        env=_GOVERNED_OFF_ENV,
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
    real = RealLocalChildRunner(env=_GOVERNED_OFF_ENV, provider_config=_provider_config(), runner=fake)
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
    real = RealLocalChildRunner(env=_GOVERNED_OFF_ENV, provider_config=_provider_config(), runner=fake)
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
    runner = RealLocalChildRunner(env=_GOVERNED_OFF_ENV, runner=fake)  # no provider_config, no key

    output = asyncio.run(runner.run_child(_request()))

    assert output["status"] == "blocked"
    assert output["summary"] == _DEGRADE_KEY_MISSING
    assert fake.calls == 0


def test_run_child_blocks_on_unknown_model_tier() -> None:
    fake = _FakeRunner()
    runner = RealLocalChildRunner(env=_GOVERNED_OFF_ENV, provider_config=_provider_config(), runner=fake)

    # A model not in ModelTierRegistry.with_defaults() → blocked.
    output = asyncio.run(
        runner.run_child(_request(provider="anthropic", model="totally-made-up-9000"))
    )

    assert output["status"] == "blocked"
    assert output["summary"] == _DEGRADE_ROUTE_UNKNOWN
    assert fake.calls == 0


def test_operator_allowlist_routes_model_absent_from_registry(monkeypatch) -> None:
    """A model NOT in the registry but in the operator route allowlist → routed."""
    from magi_agent.config.env import _ALLOWED_MODEL_ROUTES_ENV

    monkeypatch.setenv(_ALLOWED_MODEL_ROUTES_ENV, "anthropic:claude-opus-4-8")
    fake = _FakeRunner()
    runner = RealLocalChildRunner(env=_GOVERNED_OFF_ENV, provider_config=_provider_config(), runner=fake)

    output = asyncio.run(runner.run_child(_request(provider="anthropic", model="claude-opus-4-8")))

    assert output["status"] == "completed"
    assert fake.calls == 1


def test_unknown_model_still_blocks_when_allowlist_unset(monkeypatch) -> None:
    """No allowlist env → an unregistered model is still blocked (unchanged)."""
    from magi_agent.config.env import _ALLOWED_MODEL_ROUTES_ENV

    monkeypatch.delenv(_ALLOWED_MODEL_ROUTES_ENV, raising=False)
    fake = _FakeRunner()
    runner = RealLocalChildRunner(env=_GOVERNED_OFF_ENV, provider_config=_provider_config(), runner=fake)

    output = asyncio.run(
        runner.run_child(_request(provider="anthropic", model="claude-does-not-exist-9000"))
    )

    assert output["status"] == "blocked"
    assert output["summary"] == _DEGRADE_ROUTE_UNKNOWN
    assert fake.calls == 0


def test_operator_allowlist_match_is_casefolded(monkeypatch) -> None:
    from magi_agent.config.env import _ALLOWED_MODEL_ROUTES_ENV

    monkeypatch.setenv(_ALLOWED_MODEL_ROUTES_ENV, "Anthropic:Claude-Opus-4-8")
    fake = _FakeRunner()
    runner = RealLocalChildRunner(env=_GOVERNED_OFF_ENV, provider_config=_provider_config(), runner=fake)

    output = asyncio.run(runner.run_child(_request(provider="anthropic", model="claude-opus-4-8")))

    assert output["status"] == "completed"
    assert fake.calls == 1


def test_operator_allowed_model_routes_parser_failsafe() -> None:
    from magi_agent.config.env import (
        _ALLOWED_MODEL_ROUTES_ENV,
        operator_allowed_model_routes,
    )

    routes = operator_allowed_model_routes(
        {
            _ALLOWED_MODEL_ROUTES_ENV: (
                "Anthropic:Claude-Opus-4-8, openai:gpt-5.5 , nocolon, :nomodel, google:"
            )
        }
    )

    assert ("anthropic", "claude-opus-4-8") in routes
    assert ("openai", "gpt-5.5") in routes
    assert all(provider and model for provider, model in routes)
    assert operator_allowed_model_routes({}) == frozenset()


def test_run_child_never_raises_when_runner_raises_mid_turn() -> None:
    raising = _RaisingRunner()
    runner = RealLocalChildRunner(env=_GOVERNED_OFF_ENV, provider_config=_provider_config(), runner=raising)

    output = asyncio.run(runner.run_child(_request()))

    assert raising.calls == 1
    assert output["status"] in {"failed", "blocked"}
    assert output["summary"] == _DEGRADE_TURN_ERROR
    # No raw error text (path / token) leaks into the output.
    encoded = repr(output)
    assert "/Users/kevin/secret" not in encoded
    assert "sk-live-AAA" not in encoded


def test_boundary_sanitises_blocked_output_from_real_runner() -> None:
    """A degraded (blocked) mapping still routes through the boundary cleanly."""
    runner = RealLocalChildRunner(env=_GOVERNED_OFF_ENV, runner=_FakeRunner())  # no key → blocked
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
# I-2: google→gemini provider alias (default route must not be blocked)        #
# --------------------------------------------------------------------------- #


def test_run_child_resolves_google_provider_via_gemini_alias(monkeypatch) -> None:
    """A ``provider="google"`` child (the boundary default) with a Gemini key
    present resolves to a usable config — NOT ``child_provider_key_missing``."""
    # Gemini key present in env; provider explicitly the registry's "google".
    monkeypatch.setenv("GEMINI_API_KEY", "sk-gemini-test")
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "0")
    fake = _FakeRunner(text="ANSWER: gemini child ran")
    runner = RealLocalChildRunner(runner=fake)  # no injected provider_config

    output = asyncio.run(runner.run_child(_request(provider="google", model="gemini-3.5-flash")))

    assert output["status"] == "completed"
    assert output["summary"] != _DEGRADE_KEY_MISSING
    assert "gemini child ran" in str(output["summary"])
    assert fake.calls == 1


def test_run_child_default_google_route_resolves_with_gemini_key(monkeypatch) -> None:
    """The historical default child route is ``google``/gemini; with a Gemini
    key it resolves rather than silently blocking."""
    monkeypatch.setenv("GEMINI_API_KEY", "sk-gemini-test")
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "0")
    fake = _FakeRunner(text="ANSWER: default route")
    # Inject the boundary-default route via the per-request override (mirrors
    # ChildRunnerConfig.child_provider="google", child_model="gemini-3.5-flash").
    runner = RealLocalChildRunner(runner=fake)

    output = asyncio.run(runner.run_child(_request(provider="google", model="gemini-3.5-flash")))

    assert output["status"] == "completed"
    assert output["summary"] != _DEGRADE_KEY_MISSING


# --------------------------------------------------------------------------- #
# I-1: validated/normalised route threaded into litellm re-pin                  #
# --------------------------------------------------------------------------- #


def test_run_child_uses_canonical_lowercase_litellm_route(monkeypatch) -> None:
    """A mixed-case request ``model`` resolves to the canonical lowercase
    litellm route (the registry-validated route, not the raw input)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-test")
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "0")

    captured: dict[str, object] = {}

    def _fake_build(config, **kwargs):  # noqa: ANN001, ANN003
        captured["litellm_model"] = config.litellm_model
        captured["provider"] = config.provider
        captured["model"] = config.model
        return _RecordingRunner(text="ANSWER: canonical route")

    # Patch build_cli_model_runner so no real model/network is built; capture
    # the ProviderConfig the runner re-pinned from the validated route.
    monkeypatch.setattr("magi_agent.cli.real_runner.build_cli_model_runner", _fake_build)

    runner = RealLocalChildRunner()  # no injected runner → goes through build
    output = asyncio.run(
        runner.run_child(_request(provider="Anthropic", model="Claude-Sonnet-4-6"))
    )

    assert output["status"] == "completed"
    # Canonical (casefolded) litellm route — never the mixed-case input.
    assert captured["model"] == "claude-sonnet-4-6"
    assert captured["litellm_model"] == "anthropic/claude-sonnet-4-6"


# --------------------------------------------------------------------------- #
# I-3: budget_ms turn timeout honoured + never raises                          #
# --------------------------------------------------------------------------- #


def test_run_child_times_out_on_budget_ms_and_never_raises() -> None:
    slow = _SlowRunner(sleep_s=5.0)
    runner = RealLocalChildRunner(env=_GOVERNED_OFF_ENV, provider_config=_provider_config(), runner=slow)

    # Tiny budget → the 5s runner must be cut off and degrade (never raise).
    output = asyncio.run(runner.run_child(_request(budgetMs=20)))

    assert slow.calls == 1
    assert output["status"] in {"failed", "blocked"}
    assert output["summary"] == _DEGRADE_TIMEOUT


def test_run_child_no_budget_ms_fast_child_completes() -> None:
    """Without an explicit budget, a fast child still completes — the default
    ceiling bounds the turn but does not cut off a child that finishes in time."""
    fake = _FakeRunner(text="ANSWER: default-bound ok")
    runner = RealLocalChildRunner(env=_GOVERNED_OFF_ENV, provider_config=_provider_config(), runner=fake)

    output = asyncio.run(runner.run_child(_request()))  # budget_ms defaults to 0

    assert output["status"] == "completed"
    assert "default-bound ok" in str(output["summary"])


def test_run_child_no_budget_ms_still_times_out_on_hang() -> None:
    """Regression: a child with NO budget_ms must still be bounded by the default
    ceiling (lowered by ``MAGI_MODEL_TIMEOUT_S``) so a hung child degrades to
    ``child_turn_timeout`` instead of hanging the parent turn forever."""
    slow = _SlowRunner(sleep_s=2.0)
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        runner=slow,
        env={**_GOVERNED_OFF_ENV, "MAGI_MODEL_TIMEOUT_S": "0.1"},
    )

    # No budget_ms on the request → previously ran unbounded (the parent turn
    # hung forever). Now the default ceiling (0.1s) cuts the 2s runner off →
    # degrade, never hang or raise.
    output = asyncio.run(runner.run_child(_request()))

    assert slow.calls == 1
    assert output["status"] in {"failed", "blocked"}
    assert output["summary"] == _DEGRADE_TIMEOUT


def test_turn_timeout_no_budget_uses_tight_default_not_ceiling() -> None:
    """A child with NO budget_ms is bounded by the TIGHT default, not the full
    600s ceiling: a runaway delegated subtask must not burn the whole ceiling."""
    runner = RealLocalChildRunner(provider_config=_provider_config(), env={})
    timeout = runner._turn_timeout_s(_request())  # budget_ms defaults to 0
    assert timeout == _DEFAULT_CHILD_TURN_TIMEOUT_S
    assert timeout < _MAX_TURN_TIMEOUT_S


def test_turn_timeout_no_budget_env_override() -> None:
    """MAGI_CHILD_TURN_TIMEOUT_S tunes the no-budget default (clamped to ceiling)."""
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        env={"MAGI_CHILD_TURN_TIMEOUT_S": "42"},
    )
    assert runner._turn_timeout_s(_request()) == 42.0

    # A huge override is still clamped to the hard ceiling.
    runner_hi = RealLocalChildRunner(
        provider_config=_provider_config(),
        env={"MAGI_CHILD_TURN_TIMEOUT_S": "99999"},
    )
    assert runner_hi._turn_timeout_s(_request()) == _MAX_TURN_TIMEOUT_S


def test_turn_timeout_explicit_budget_ms_still_honoured() -> None:
    """An explicit positive budget_ms still wins over the default (clamped)."""
    runner = RealLocalChildRunner(provider_config=_provider_config(), env={})
    assert runner._turn_timeout_s(_request(budget_ms=5000)) == 5.0


def test_turn_timeout_no_budget_model_timeout_clamps_default() -> None:
    """MAGI_MODEL_TIMEOUT_S lowers the no-budget default below its own value."""
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        env={"MAGI_MODEL_TIMEOUT_S": "60"},  # below the 300s default
    )
    assert runner._turn_timeout_s(_request()) == 60.0


def test_run_child_propagates_cancellation() -> None:
    """``asyncio.CancelledError`` from the turn must PROPAGATE, never become a
    degraded mapping."""

    class _CancellingRunner:
        def __init__(self) -> None:
            self.calls = 0

        async def run_async(self, **kwargs: object) -> AsyncGenerator[object, None]:
            self.calls += 1
            raise asyncio.CancelledError()
            yield  # pragma: no cover - async generator marker

    cancelling = _CancellingRunner()
    runner = RealLocalChildRunner(env=_GOVERNED_OFF_ENV, provider_config=_provider_config(), runner=cancelling)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runner.run_child(_request()))
    assert cancelling.calls == 1


# --------------------------------------------------------------------------- #
# PR1 (doc 07): toolset injection + tool-call evidence into live child runner   #
# --------------------------------------------------------------------------- #


def test_run_child_readonly_profile_forwards_readonly_tools_to_builder(
    monkeypatch,
) -> None:
    """A ``readonly`` profile builds the read-only toolset and forwards a
    NON-EMPTY ``tools`` list to ``build_cli_model_runner`` (no more tools=[])."""
    from magi_agent.runtime.child_toolset import READONLY_TOOL_NAMES

    captured: dict[str, object] = {}

    class _NamedTool:
        def __init__(self, name: str) -> None:
            self.name = name

    def _fake_build_tools(**kwargs):  # noqa: ANN003
        # Return a full-ish core toolset (read-only names + a mutating tool) so
        # the runner's read-only FILTER is exercised.
        names = [*READONLY_TOOL_NAMES, "FileWrite", "Bash"]
        return [_NamedTool(n) for n in names]

    def _fake_build_runner(config, **kwargs):  # noqa: ANN001, ANN003
        captured["tools"] = kwargs.get("tools")
        return _RecordingRunner(text="ANSWER: readonly child ran")

    monkeypatch.setattr("magi_agent.cli.tool_runtime.build_cli_adk_tools", _fake_build_tools)
    monkeypatch.setattr("magi_agent.cli.real_runner.build_cli_model_runner", _fake_build_runner)

    runner = RealLocalChildRunner(env=_GOVERNED_OFF_ENV, provider_config=_provider_config(), toolset_profile="readonly")
    output = asyncio.run(runner.run_child(_request()))

    assert output["status"] == "completed"
    tool_names = [getattr(t, "name", None) for t in captured["tools"]]
    # Read-only inspection tools are forwarded ...
    assert "FileRead" in tool_names
    assert "Glob" in tool_names
    assert "Grep" in tool_names
    # ... and mutating tools are filtered OUT (no tool escalation).
    assert "FileWrite" not in tool_names
    assert "Bash" not in tool_names


def test_run_child_readonly_profile_builds_instruction_without_memory_projection(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Readonly children may use the parent workspace for file reads, but must
    not build a memory snapshot from production-mounted workspace paths."""

    captured: dict[str, object] = {}

    class _NamedTool:
        def __init__(self, name: str) -> None:
            self.name = name

    def _fake_build_tools(**kwargs):  # noqa: ANN003
        return [_NamedTool("FileRead")]

    def _fake_build_runner(config, **kwargs):  # noqa: ANN001, ANN003
        captured.update(kwargs)
        return _RecordingRunner(text="ANSWER: readonly child ran")

    monkeypatch.setattr("magi_agent.cli.tool_runtime.build_cli_adk_tools", _fake_build_tools)
    monkeypatch.setattr("magi_agent.cli.real_runner.build_cli_model_runner", _fake_build_runner)

    runner = RealLocalChildRunner(
        env=_GOVERNED_OFF_ENV,
        provider_config=_provider_config(),
        toolset_profile="readonly",
        workspace_root=str(tmp_path),
    )

    output = asyncio.run(runner.run_child(_request()))

    assert output["status"] == "completed"
    assert captured["workspace_root"] == str(tmp_path)
    assert captured["instruction"] is None
    assert captured["memory_mode"] == "incognito"


def test_readonly_child_toolset_does_not_build_full_local_handlers(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Readonly child tools must not initialize full local writable surfaces."""

    import magi_agent.cli.tool_runtime as tool_runtime

    def fail_full_handler_bind(*args: object, **kwargs: object) -> None:
        raise AssertionError("full local handlers should not be built")

    monkeypatch.setattr(
        tool_runtime,
        "bind_cli_local_full_tool_handlers",
        fail_full_handler_bind,
    )

    runner = RealLocalChildRunner(
        env=_GOVERNED_OFF_ENV,
        toolset_profile="readonly",
        workspace_root=str(tmp_path),
    )

    tools, collector = runner._resolve_turn_toolset("child-session-readonly")

    assert collector is not None
    # PR-N (Kevin 0.1.91 SOTA-spawn debug): ``Calculation`` joined the readonly
    # allowlist as a pure, deterministic, side-effect-free helper so spawn
    # children that try to use a tool for arithmetic stop crashing with
    # ``Tool 'Calculation' not found``. Source-inspection tools (the original
    # four) still surface unchanged.
    assert {str(tool.name) for tool in tools} == {
        "FileRead",
        "Glob",
        "Grep",
        "GitDiff",
        "Calculation",
    }


def test_run_child_readonly_profile_promotes_tool_receipts_to_evidence_refs() -> None:
    """When a toolset runs, the collected tool-call receipts surface as the
    child's ``evidenceRefs`` (promoted to the envelope by the boundary)."""

    class _FakeCollector:
        """Stands in for ``LocalToolEvidenceCollector`` — yields public refs."""

        def evidence_refs_for_session(self, session_id: str) -> tuple[str, ...]:
            return ("evidence:tool-call-1", "evidence:tool-call-2")

    fake = _FakeRunner(text="ANSWER: read the file")
    runner = RealLocalChildRunner(
        env=_GOVERNED_OFF_ENV,
        provider_config=_provider_config(),
        runner=fake,
        toolset_profile="readonly",
        evidence_collector=_FakeCollector(),
    )

    output = asyncio.run(runner.run_child(_request()))

    assert output["status"] == "completed"
    assert tuple(output["evidenceRefs"]) == (
        "evidence:tool-call-1",
        "evidence:tool-call-2",
    )


def test_readonly_evidence_refs_promoted_through_boundary_envelope() -> None:
    """e2e: a read-only child's tool receipts become non-empty envelope
    ``evidence_refs`` after passing through the boundary sanitiser."""

    class _FakeCollector:
        def evidence_refs_for_session(self, session_id: str) -> tuple[str, ...]:
            return ("evidence:child-read-1",)

    fake = _FakeRunner(text="ANSWER: boundary readonly")
    real = RealLocalChildRunner(
        env=_GOVERNED_OFF_ENV,
        provider_config=_provider_config(),
        runner=fake,
        toolset_profile="readonly",
        evidence_collector=_FakeCollector(),
    )
    boundary = LocalChildRunnerBoundary(
        ChildRunnerConfig(enabled=True, liveChildRunnerEnabled=True),
        child_runner=real,
    )

    result = asyncio.run(boundary.run(_request()))

    assert result.status == "ok"
    assert result.envelope is not None
    assert result.envelope.status == "completed"
    # The boundary re-issues refs but only when the child emitted ≥1 evidence
    # ref — so a non-empty tuple proves the tool-receipt promotion worked.
    assert len(result.envelope.evidence_refs) >= 1


def test_run_child_default_profile_is_byte_identical_empty_toolset() -> None:
    """REGRESSION: with no toolset_profile (default ``none``) the output keys/
    values are unchanged — empty toolset, empty refs (v1 byte-identical)."""
    fake = _FakeRunner(text="ANSWER: text only child")
    runner = RealLocalChildRunner(env=_GOVERNED_OFF_ENV, provider_config=_provider_config(), runner=fake)

    output = asyncio.run(runner.run_child(_request()))

    assert set(output.keys()) == {
        "childExecutionId",
        "status",
        "summary",
        "evidenceRefs",
        "artifactRefs",
        "auditEventRefs",
    }
    assert output["status"] == "completed"
    assert output["evidenceRefs"] == ()
    assert output["artifactRefs"] == ()
    assert output["auditEventRefs"] == ()


def test_run_child_none_profile_forwards_empty_toolset_to_builder(
    monkeypatch,
) -> None:
    """REGRESSION: default ``none`` profile keeps the historical ``tools=[]``
    forwarded to ``build_cli_model_runner`` (no read-only tools leak in)."""
    captured: dict[str, object] = {}

    def _fake_build_runner(config, **kwargs):  # noqa: ANN001, ANN003
        captured["tools"] = kwargs.get("tools")
        return _RecordingRunner(text="ANSWER: none profile")

    monkeypatch.setattr("magi_agent.cli.real_runner.build_cli_model_runner", _fake_build_runner)

    runner = RealLocalChildRunner(env=_GOVERNED_OFF_ENV, provider_config=_provider_config())  # default none
    output = asyncio.run(runner.run_child(_request()))

    assert output["status"] == "completed"
    assert captured["tools"] == []


# --------------------------------------------------------------------------- #
# Marker contract                                                             #
# --------------------------------------------------------------------------- #


def test_real_local_child_runner_declares_live_provider_marker() -> None:
    assert RealLocalChildRunner.openmagi_live_provider is True
    assert RealLocalChildRunner(env=_GOVERNED_OFF_ENV, runner=_FakeRunner()).openmagi_live_provider is True


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


# ---------------------------------------------------------------------------
# Fix F: missing-tool streak fast-fail (legacy path, end-to-end via run_child)
# ---------------------------------------------------------------------------


class _FakeFunctionResponse:
    def __init__(self, name: str, response: dict) -> None:
        self.name = name
        self.id = f"call-{name}"
        self.response = response


class _FakeToolPart:
    def __init__(self, function_response: object) -> None:
        self.text = None
        self.function_call = None
        self.function_response = function_response


class _MissingToolSpiralRunner:
    """Emits one text event, then `n` missing-tool function_response events
    (name-cycling), mimicking a child that hallucinates tools it lacks."""

    openmagi_live_provider = True

    def __init__(self, n: int = 5, *, tools: object = None, **kwargs: object) -> None:
        self._n = n
        self.calls = 0

    async def run_async(self, **kwargs: object) -> AsyncGenerator[object, None]:
        self.calls += 1
        yield _FakeEvent("partial answer so far")
        names = ["XLSXRead", "BrowserTask"]
        codes = ["tool_not_found", "tool_not_exposed"]
        for i in range(self._n):
            part = _FakeToolPart(
                _FakeFunctionResponse(
                    names[i % 2],
                    {
                        "response_type": "MAGI_TOOL_NOT_FOUND_SOFT_FAIL",
                        "status": "error",
                        "error_code": codes[i % 2],
                    },
                )
            )

            class _E:
                content = type("C", (), {"parts": [part]})()

            yield _E()


def test_legacy_missing_tool_streak_trips_with_partial(monkeypatch) -> None:
    """A legacy child spiraling on missing tools trips fast with the typed
    reason and preserves its best-effort text (composes with #1458)."""
    runner = _MissingToolSpiralRunner(n=5)
    child = RealLocalChildRunner(
        provider_config=_provider_config(),
        runner=runner,
        env={**_GOVERNED_OFF_ENV, "MAGI_CHILD_MISSING_TOOL_STREAK_CAP": "4"},
    )
    output = asyncio.run(child.run_child(_request()))

    assert output["status"] == "failed"
    assert output["summary"] == "child_llm_missing_tool_streak_exhausted"
    # Best-effort partial answer preserved on the separate channel (#1458).
    assert output.get("partialSummary") == "partial answer so far"


def test_legacy_missing_tool_streak_cap_zero_disables(monkeypatch) -> None:
    """cap=0 disables the guard: the child runs to normal completion."""
    runner = _MissingToolSpiralRunner(n=5)
    child = RealLocalChildRunner(
        provider_config=_provider_config(),
        runner=runner,
        env={**_GOVERNED_OFF_ENV, "MAGI_CHILD_MISSING_TOOL_STREAK_CAP": "0"},
    )
    output = asyncio.run(child.run_child(_request()))
    # No trip; the turn completed (text was collected).
    assert output["status"] == "completed"
    assert "partial answer so far" in str(output["summary"])


def test_derive_child_session_id_matches_runner_formula() -> None:
    # The shared helper MUST produce the exact id the runner assigns, otherwise
    # the child_started linkage would point at a session that never exists.
    req = ChildTaskRequest(
        parentExecutionId="agent:main:app:demo:50",
        turnId="turn-x",
        taskId="task-9",
        objective="do it",
    )
    assert derive_child_session_id(
        parent_execution_id="agent:main:app:demo:50",
        turn_id="turn-x",
        task_id="task-9",
    ) == RealLocalChildRunner._child_session_id(req)


def test_derive_child_session_id_uses_sentinels_for_missing_parts() -> None:
    # Byte-stable fallback seed (parent/turn/task) so ids stay deterministic.
    import hashlib

    seed = "parent:turn:task"
    expected = "child-session-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    assert (
        derive_child_session_id(parent_execution_id=None, turn_id=None, task_id=None)
        == expected
    )
