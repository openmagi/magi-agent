"""Tests for recipeRefs → pinned_recipe_pack_ids threading in RealLocalChildRunner (TY2).

Coverage:
- T1: Flag OFF + metadata recipeRefs present → both call sites receive pinned_recipe_pack_ids=()
      (byte-identical; pins ignored when flag is off).
- T2: Flag ON + metadata recipeRefs=("openmagi.research","openmagi.dev-coding") → governed-path
      build_headless_runtime receives pinned_recipe_pack_ids=("openmagi.research","openmagi.dev-coding").
- T3: Flag ON + no recipeRefs key in metadata → governed call receives pinned_recipe_pack_ids=().
- T4: Flag ON + metadata recipeRefs present → legacy-path build_cli_model_runner receives
      pinned_recipe_pack_ids=("openmagi.research","openmagi.dev-coding").
- T5: Flag OFF + metadata recipeRefs present → legacy-path build_cli_model_runner receives
      pinned_recipe_pack_ids=() (byte-identical).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import pytest

import magi_agent.runtime.governed_turn as governed_turn_mod
from magi_agent.runtime.child_runner_boundary import ChildTaskRequest
from magi_agent.runtime.child_runner_live import RealLocalChildRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    "MAGI_SPAWN_RECIPE_BIND_ENABLED",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path) -> None:
    """Hermetic: no real key / config / recipe-bind flag leakage."""
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))


def _request(metadata: dict[str, object] | None = None, **overrides: object) -> ChildTaskRequest:
    data: dict[str, object] = {
        "parentExecutionId": "parent-exec-ty2",
        "turnId": "turn-ty2-1",
        "taskId": "task-ty2-1",
        "objective": "TY2 recipe-refs threading test.",
        "role": "research",
        "delivery": "return",
    }
    if metadata is not None:
        data["metadata"] = metadata
    data.update(overrides)
    return ChildTaskRequest(**data)


def _provider_config(api_key: str = "sk-test") -> object:
    from magi_agent.cli.providers import ProviderConfig

    return ProviderConfig(provider="anthropic", model="claude-sonnet-4-6", api_key=api_key)


# ---------------------------------------------------------------------------
# Governed-path fake helpers
# ---------------------------------------------------------------------------


async def _fake_governed_stream() -> AsyncGenerator[Any, None]:
    """Minimal governed stream: single terminal EngineResult."""
    from magi_agent.cli.contracts import EngineResult, Terminal

    yield EngineResult(terminal=Terminal.completed)


# ---------------------------------------------------------------------------
# T1: Flag OFF + recipeRefs in metadata → governed call receives ()
# ---------------------------------------------------------------------------


def test_flag_off_governed_path_pins_are_empty(monkeypatch) -> None:
    """Flag OFF: even when metadata carries recipeRefs, governed build_headless_runtime
    receives pinned_recipe_pack_ids=() (byte-identical dormancy)."""
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")
    # MAGI_SPAWN_RECIPE_BIND_ENABLED explicitly OFF (promoted to default-ON).
    monkeypatch.setenv("MAGI_SPAWN_RECIPE_BIND_ENABLED", "0")

    captured_kwargs: list[dict[str, object]] = []

    def _fake_build_headless_runtime(**kwargs: object) -> object:
        captured_kwargs.append(dict(kwargs))
        return object()

    async def _fake_run_governed_turn(
        ctx: object, *, runtime: object | None = None, cancel: object | None = None
    ) -> AsyncGenerator[Any, None]:
        from magi_agent.cli.contracts import EngineResult, Terminal

        yield EngineResult(terminal=Terminal.completed)

    monkeypatch.setattr("magi_agent.cli.wiring.build_headless_runtime", _fake_build_headless_runtime)
    monkeypatch.setattr(governed_turn_mod, "run_governed_turn", _fake_run_governed_turn)

    runner = RealLocalChildRunner(provider_config=_provider_config())
    asyncio.run(runner.run_child(_request(metadata={"recipeRefs": ("openmagi.research",)})))

    assert len(captured_kwargs) == 1
    assert captured_kwargs[0].get("pinned_recipe_pack_ids") == ()


# ---------------------------------------------------------------------------
# T2: Flag ON + recipeRefs present → governed call receives the refs
# ---------------------------------------------------------------------------


def test_flag_on_governed_path_pins_flow_from_metadata(monkeypatch) -> None:
    """Flag ON + recipeRefs in metadata → build_headless_runtime receives them as
    pinned_recipe_pack_ids."""
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")
    monkeypatch.setenv("MAGI_SPAWN_RECIPE_BIND_ENABLED", "1")

    captured_kwargs: list[dict[str, object]] = []

    def _fake_build_headless_runtime(**kwargs: object) -> object:
        captured_kwargs.append(dict(kwargs))
        return object()

    async def _fake_run_governed_turn(
        ctx: object, *, runtime: object | None = None, cancel: object | None = None
    ) -> AsyncGenerator[Any, None]:
        from magi_agent.cli.contracts import EngineResult, Terminal

        yield EngineResult(terminal=Terminal.completed)

    monkeypatch.setattr("magi_agent.cli.wiring.build_headless_runtime", _fake_build_headless_runtime)
    monkeypatch.setattr(governed_turn_mod, "run_governed_turn", _fake_run_governed_turn)

    runner = RealLocalChildRunner(provider_config=_provider_config())
    asyncio.run(
        runner.run_child(
            _request(metadata={"recipeRefs": ("openmagi.research", "openmagi.dev-coding")})
        )
    )

    assert len(captured_kwargs) == 1
    assert captured_kwargs[0].get("pinned_recipe_pack_ids") == (
        "openmagi.research",
        "openmagi.dev-coding",
    )


# ---------------------------------------------------------------------------
# T3: Flag ON + no recipeRefs key → governed call receives ()
# ---------------------------------------------------------------------------


def test_flag_on_no_recipe_refs_governed_pins_empty(monkeypatch) -> None:
    """Flag ON but metadata has no recipeRefs key → governed build_headless_runtime
    receives pinned_recipe_pack_ids=() (no-op, byte-identical)."""
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")
    monkeypatch.setenv("MAGI_SPAWN_RECIPE_BIND_ENABLED", "1")

    captured_kwargs: list[dict[str, object]] = []

    def _fake_build_headless_runtime(**kwargs: object) -> object:
        captured_kwargs.append(dict(kwargs))
        return object()

    async def _fake_run_governed_turn(
        ctx: object, *, runtime: object | None = None, cancel: object | None = None
    ) -> AsyncGenerator[Any, None]:
        from magi_agent.cli.contracts import EngineResult, Terminal

        yield EngineResult(terminal=Terminal.completed)

    monkeypatch.setattr("magi_agent.cli.wiring.build_headless_runtime", _fake_build_headless_runtime)
    monkeypatch.setattr(governed_turn_mod, "run_governed_turn", _fake_run_governed_turn)

    runner = RealLocalChildRunner(provider_config=_provider_config())
    # metadata present but no recipeRefs key
    asyncio.run(runner.run_child(_request(metadata={"spawnDepth": 1})))

    assert len(captured_kwargs) == 1
    assert captured_kwargs[0].get("pinned_recipe_pack_ids") == ()


# ---------------------------------------------------------------------------
# T4: Flag ON + recipeRefs present → legacy-path build_cli_model_runner receives refs
# ---------------------------------------------------------------------------


def test_flag_on_legacy_path_pins_flow_from_metadata(monkeypatch) -> None:
    """Flag ON + recipeRefs in metadata → legacy build_cli_model_runner receives
    pinned_recipe_pack_ids with the supplied refs."""
    # Governed flag explicitly OFF (promoted to default-ON) → legacy path runs
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "0")
    monkeypatch.setenv("MAGI_SPAWN_RECIPE_BIND_ENABLED", "1")

    captured_kwargs: dict[str, object] = {}

    class _RecordingRunner:
        async def run_async(self, **kwargs: object) -> AsyncGenerator[object, None]:
            from magi_agent.runtime.child_runner_live import _FakeContent, _FakePart  # type: ignore[attr-defined]
            yield type("Ev", (), {"content": type("C", (), {"parts": [type("P", (), {"text": "ok"})()]})()})()

    def _fake_build_cli_model_runner(config: object, **kwargs: object) -> object:
        captured_kwargs.update(kwargs)
        return _RecordingRunner()

    monkeypatch.setattr(
        "magi_agent.cli.real_runner.build_cli_model_runner", _fake_build_cli_model_runner
    )

    runner = RealLocalChildRunner(provider_config=_provider_config())
    asyncio.run(
        runner.run_child(
            _request(metadata={"recipeRefs": ("openmagi.research", "openmagi.dev-coding")})
        )
    )

    assert captured_kwargs.get("pinned_recipe_pack_ids") == (
        "openmagi.research",
        "openmagi.dev-coding",
    )


# ---------------------------------------------------------------------------
# T5: Flag OFF + recipeRefs present → legacy-path receives ()
# ---------------------------------------------------------------------------


def test_flag_off_legacy_path_pins_are_empty(monkeypatch) -> None:
    """Flag OFF: legacy build_cli_model_runner receives pinned_recipe_pack_ids=()
    (byte-identical dormancy — same as if no recipeRefs were present)."""
    # Governed + bind flags explicitly OFF (both promoted to default-ON) →
    # legacy path with dormant pins.
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "0")
    monkeypatch.setenv("MAGI_SPAWN_RECIPE_BIND_ENABLED", "0")

    captured_kwargs: dict[str, object] = {}

    class _RecordingRunner:
        async def run_async(self, **kwargs: object) -> AsyncGenerator[object, None]:
            yield type("Ev", (), {"content": type("C", (), {"parts": [type("P", (), {"text": "ok"})()]})()})()

    def _fake_build_cli_model_runner(config: object, **kwargs: object) -> object:
        captured_kwargs.update(kwargs)
        return _RecordingRunner()

    monkeypatch.setattr(
        "magi_agent.cli.real_runner.build_cli_model_runner", _fake_build_cli_model_runner
    )

    runner = RealLocalChildRunner(provider_config=_provider_config())
    asyncio.run(runner.run_child(_request(metadata={"recipeRefs": ("openmagi.research",)})))

    assert captured_kwargs.get("pinned_recipe_pack_ids") == ()
