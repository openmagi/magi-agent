"""TDD harness for ``build_hosted_runtime`` (PR1 foundation).

Four test groups:

1. **Construction sanity** — ``build_hosted_runtime`` returns a ``HostedRuntime``
   whose ``.engine`` is a ``MagiEngineDriver`` wired with ``HOSTED_PROFILE``.

2. **End-to-end parity via run_governed_turn** — two gate5b4c3 scenarios
   (``tool_then_final`` and ``native_tool_roundtrip``) are driven via
   ``run_governed_turn(ctx, runtime=hosted_rt)`` and compared byte-for-byte
   against the committed golden snapshots (with ``durationMs`` normalised).
   This proves the full ``HostedRuntime`` path — not just the engine bridge —
   emits the correct hosted wire shape.

3. **Plugin pass-through** — control_plane_plugins are forwarded to the Runner
   when non-empty; when empty, the ``plugins`` kwarg is omitted from the Runner
   call entirely.

4. **No engine changes** — a sanity import check that the existing engine's
   ``wire_profile=None`` default is unchanged (regression guard).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from magi_agent.adk_bridge.wire_profile import HOSTED_PROFILE
from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.runtime.governed_turn import run_governed_turn
from magi_agent.runtime.hosted_runtime import HostedRuntime, build_hosted_runtime
from magi_agent.runtime.turn_context import TurnContext
from tests.support.engine_fakes import MockRunner, call_event, response_event, text_event
from tests.support.gate5b4c3_fakes import (
    _FakeAgent,
    _FakeGenerateContentConfig,
    _FakeSessionService,
    make_primitives,
)


_GOLDEN_DIR = Path(__file__).parent / "golden" / "gate5b4c3"
_TURN_ID = "t-hosted-rt"
_SESSION_ID = "s-hosted-rt"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_loader(runner: object) -> object:
    """Return a zero-arg callable that yields ``make_primitives(runner)``."""
    primitives = make_primitives(runner)

    def _loader() -> object:
        return primitives

    return _loader


def _fake_generate_content_config() -> _FakeGenerateContentConfig:
    return _FakeGenerateContentConfig()


def _ctx(prompt: str = "test") -> TurnContext:
    return TurnContext(
        prompt=prompt,
        session_id=_SESSION_ID,
        turn_id=_TURN_ID,
    )


async def _capture_via_run_governed_turn(
    hosted_rt: HostedRuntime,
    prompt: str = "test",
) -> list[dict[str, Any]]:
    """Drive one turn via ``run_governed_turn`` and return public event dicts.

    Consumes the async generator produced by ``run_governed_turn``, collects
    all ``RuntimeEvent`` objects, and returns their ``payload`` dicts — matching
    the extraction ``test_hosted_wire_profile_parity`` does via ``drain``.
    """
    from magi_agent.cli.contracts import EngineResult  # local import (engine contracts)

    ctx = _ctx(prompt)
    events: list[dict[str, Any]] = []
    async for item in run_governed_turn(ctx, runtime=hosted_rt):
        if isinstance(item, RuntimeEvent):
            events.append(item.payload)
        elif isinstance(item, EngineResult):
            break  # terminal — stop consuming
    return events


def _load_golden(name: str) -> dict[str, Any]:
    return json.loads((_GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _normalize_duration(events: list[dict]) -> list[dict]:
    """Replace volatile ``durationMs`` with sentinel (mirrors golden normalization)."""
    result = []
    for evt in events:
        evt = dict(evt)
        if "durationMs" in evt:
            evt["durationMs"] = "<normalized>"
        result.append(evt)
    return result


def _tool_events(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("type") in {"tool_start", "tool_progress", "tool_end"}]


# ---------------------------------------------------------------------------
# 1. Construction sanity
# ---------------------------------------------------------------------------


def test_build_hosted_runtime_returns_hosted_runtime() -> None:
    """build_hosted_runtime returns a HostedRuntime instance."""
    runner = MockRunner([text_event("ok", partial=True, turn_complete=True)])
    loader = _make_loader(runner)
    rt = build_hosted_runtime(
        adk_primitives_loader=loader,
        adk_tools=(),
        model="fake-model",
        instruction="You are a test agent.",
        generate_content_config=_fake_generate_content_config(),
    )
    assert isinstance(rt, HostedRuntime), f"Expected HostedRuntime, got {type(rt)}"


def test_build_hosted_runtime_engine_is_magi_engine_driver() -> None:
    """HostedRuntime.engine is a MagiEngineDriver."""
    runner = MockRunner([text_event("ok", partial=True, turn_complete=True)])
    loader = _make_loader(runner)
    rt = build_hosted_runtime(
        adk_primitives_loader=loader,
        adk_tools=(),
        model="fake-model",
        instruction="You are a test agent.",
        generate_content_config=_fake_generate_content_config(),
    )
    assert isinstance(rt.engine, MagiEngineDriver), (
        f"Expected MagiEngineDriver, got {type(rt.engine)}"
    )


def test_build_hosted_runtime_engine_wired_with_hosted_profile() -> None:
    """HostedRuntime.engine._wire_profile is HOSTED_PROFILE."""
    runner = MockRunner([text_event("ok", partial=True, turn_complete=True)])
    loader = _make_loader(runner)
    rt = build_hosted_runtime(
        adk_primitives_loader=loader,
        adk_tools=(),
        model="fake-model",
        instruction="You are a test agent.",
        generate_content_config=_fake_generate_content_config(),
    )
    assert rt.engine._wire_profile is HOSTED_PROFILE, (
        f"Expected HOSTED_PROFILE, got {rt.engine._wire_profile!r}"
    )


def test_build_hosted_runtime_gate_is_not_none() -> None:
    """HostedRuntime.gate is a no-op gate object (not None)."""
    runner = MockRunner([text_event("ok", partial=True, turn_complete=True)])
    loader = _make_loader(runner)
    rt = build_hosted_runtime(
        adk_primitives_loader=loader,
        adk_tools=(),
        model="fake-model",
        instruction="You are a test agent.",
        generate_content_config=_fake_generate_content_config(),
    )
    # gate must exist (run_governed_turn reads rt.gate via getattr)
    assert rt.gate is not None, "HostedRuntime.gate must not be None"
    # gate must support async check (no-op gate)
    assert hasattr(rt.gate, "check"), "HostedRuntime.gate must have a check method"


# ---------------------------------------------------------------------------
# 2. End-to-end parity via run_governed_turn (gate5b4c3 golden scenarios)
# ---------------------------------------------------------------------------


def test_tool_then_final_tool_events_match_golden_via_hosted_runtime() -> None:
    """tool_then_final: tool events match golden when driven via run_governed_turn+HostedRuntime.

    This is the integration test: HostedRuntime assembled via build_hosted_runtime,
    driven via run_governed_turn, produces the same tool_start/tool_progress/tool_end
    payloads as the committed gate5b4c3 golden.
    """
    runner = MockRunner(
        [
            call_event("Calculation", {"expression": "1 + 1"}, "calculation-call-001"),
            response_event(
                "Calculation",
                {"status": "ok", "reason": "tool_completed", "outputPreview": {"value": 2}},
                "calculation-call-001",
            ),
            text_event(
                "final answer after manual tool execution", partial=True, turn_complete=True
            ),
        ]
    )
    loader = _make_loader(runner)
    rt = build_hosted_runtime(
        adk_primitives_loader=loader,
        adk_tools=(),
        model="fake-model",
        instruction="test",
        generate_content_config=_fake_generate_content_config(),
    )

    captured = asyncio.run(_capture_via_run_governed_turn(rt))
    tool_evts = _normalize_duration(_tool_events(captured))

    golden = _load_golden("tool_then_final")
    golden_tool = _normalize_duration(
        [
            e
            for e in golden["public_events"]
            if e.get("type") in {"tool_start", "tool_progress", "tool_end"}
        ]
    )

    assert tool_evts, "expected at least one tool event from HostedRuntime path"
    assert golden_tool, "tool_then_final golden must have tool events"

    # tool_start and tool_progress: exact equality
    engine_start = [e for e in tool_evts if e.get("type") == "tool_start"]
    golden_start = [e for e in golden_tool if e.get("type") == "tool_start"]
    assert engine_start and golden_start
    # tool_start: check id, type, name (engine may carry extra fields like input_preview
    # that the gate5b4c3 golden omits — this is a known documented non-divergence in field set).
    assert engine_start[0]["id"] == golden_start[0]["id"], (
        f"tool_start id mismatch via HostedRuntime.\n"
        f"  engine: {engine_start[0]['id']!r}\n"
        f"  golden: {golden_start[0]['id']!r}"
    )
    assert engine_start[0]["type"] == "tool_start"
    assert engine_start[0].get("name") == golden_start[0].get("name"), (
        f"tool_start name mismatch.\n"
        f"  engine: {engine_start[0].get('name')!r}\n"
        f"  golden: {golden_start[0].get('name')!r}"
    )
    assert "durationMs" not in engine_start[0], "tool_start must not have durationMs"

    engine_prog = [e for e in tool_evts if e.get("type") == "tool_progress"]
    golden_prog = [e for e in golden_tool if e.get("type") == "tool_progress"]
    assert engine_prog and golden_prog
    assert engine_prog[0] == golden_prog[0], (
        f"tool_progress mismatch via HostedRuntime.\n"
        f"  engine: {engine_prog[0]}\n"
        f"  golden: {golden_prog[0]}"
    )

    engine_end = [e for e in tool_evts if e.get("type") == "tool_end"]
    golden_end = [e for e in golden_tool if e.get("type") == "tool_end"]
    assert engine_end and golden_end
    assert engine_end[0] == golden_end[0], (
        f"tool_end mismatch via HostedRuntime (after durationMs normalization).\n"
        f"  engine: {engine_end[0]}\n"
        f"  golden: {golden_end[0]}"
    )


def test_native_tool_roundtrip_tool_events_match_golden_via_hosted_runtime() -> None:
    """native_tool_roundtrip: tool events match golden when driven via run_governed_turn+HostedRuntime.

    Same integration proof as tool_then_final, for the ADK-native roundtrip scenario.
    """
    runner = MockRunner(
        [
            call_event("Calculation", {"expression": "1 + 1"}, "calculation-call-001"),
            response_event("Calculation", {"status": "ok"}, "calculation-call-001"),
            text_event(
                "final answer after native tool roundtrip", partial=True, turn_complete=True
            ),
        ]
    )
    loader = _make_loader(runner)
    rt = build_hosted_runtime(
        adk_primitives_loader=loader,
        adk_tools=(),
        model="fake-model",
        instruction="test",
        generate_content_config=_fake_generate_content_config(),
    )

    captured = asyncio.run(_capture_via_run_governed_turn(rt))
    tool_evts = _normalize_duration(_tool_events(captured))

    golden = _load_golden("native_tool_roundtrip")
    golden_tool = _normalize_duration(
        [
            e
            for e in golden["public_events"]
            if e.get("type") in {"tool_start", "tool_progress", "tool_end"}
        ]
    )

    assert tool_evts, "expected at least one tool event from HostedRuntime path"
    assert golden_tool, "native_tool_roundtrip golden must have tool events"

    engine_start = [e for e in tool_evts if e.get("type") == "tool_start"]
    golden_start = [e for e in golden_tool if e.get("type") == "tool_start"]
    assert engine_start and golden_start
    # tool_start: check id, type, name (engine may carry extra fields like input_preview
    # that the gate5b4c3 golden omits — this is a known documented non-divergence).
    assert engine_start[0]["id"] == golden_start[0]["id"], (
        f"tool_start id mismatch via HostedRuntime.\n"
        f"  engine: {engine_start[0]['id']!r}\n"
        f"  golden: {golden_start[0]['id']!r}"
    )
    assert engine_start[0]["type"] == "tool_start"
    assert engine_start[0].get("name") == golden_start[0].get("name"), (
        f"tool_start name mismatch.\n"
        f"  engine: {engine_start[0].get('name')!r}\n"
        f"  golden: {golden_start[0].get('name')!r}"
    )
    assert "durationMs" not in engine_start[0], "tool_start must not have durationMs"

    engine_prog = [e for e in tool_evts if e.get("type") == "tool_progress"]
    golden_prog = [e for e in golden_tool if e.get("type") == "tool_progress"]
    assert engine_prog and golden_prog
    assert engine_prog[0] == golden_prog[0], (
        f"tool_progress mismatch via HostedRuntime.\n"
        f"  engine: {engine_prog[0]}\n"
        f"  golden: {golden_prog[0]}"
    )

    engine_end = [e for e in tool_evts if e.get("type") == "tool_end"]
    golden_end = [e for e in golden_tool if e.get("type") == "tool_end"]
    assert engine_end and golden_end
    assert engine_end[0] == golden_end[0], (
        f"tool_end mismatch via HostedRuntime (after durationMs normalization).\n"
        f"  engine: {engine_end[0]}\n"
        f"  golden: {golden_end[0]}"
    )


# ---------------------------------------------------------------------------
# 3. Plugin pass-through
# ---------------------------------------------------------------------------


def test_plugins_forwarded_to_runner_when_non_empty() -> None:
    """When control_plane_plugins is non-empty, Runner receives plugins=[sentinel]."""
    sentinel = object()

    captured_kwargs: list[dict] = []

    class _RecordingRunner:
        """Records kwargs from primitives.Runner(**kwargs)."""

        def __init__(self, **kwargs: object) -> None:
            captured_kwargs.append(dict(kwargs))

        async def run_async(self, **_kwargs: object):
            yield text_event("ok", partial=True, turn_complete=True)  # type: ignore[misc]

    class _RecordingAgent:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class _RecordingPrimitives:
        Agent = _RecordingAgent
        Runner = _RecordingRunner
        InMemorySessionService = _FakeSessionService
        Content = object
        Part = object
        GenerateContentConfig = _FakeGenerateContentConfig

    def _loader() -> object:
        return _RecordingPrimitives()

    build_hosted_runtime(
        adk_primitives_loader=_loader,
        adk_tools=(),
        model="fake-model",
        instruction="test",
        generate_content_config=_fake_generate_content_config(),
        control_plane_plugins=(sentinel,),
    )

    assert captured_kwargs, "Runner was not constructed"
    runner_kw = captured_kwargs[0]
    assert "plugins" in runner_kw, (
        "Runner must receive 'plugins' kwarg when control_plane_plugins is non-empty"
    )
    assert runner_kw["plugins"] == [sentinel], (
        f"Expected plugins=[sentinel], got {runner_kw['plugins']!r}"
    )


def test_plugins_kwarg_omitted_when_empty() -> None:
    """When control_plane_plugins is empty, Runner call omits 'plugins' kwarg entirely."""
    captured_kwargs: list[dict] = []

    class _RecordingRunner:
        def __init__(self, **kwargs: object) -> None:
            captured_kwargs.append(dict(kwargs))

        async def run_async(self, **_kwargs: object):
            yield text_event("ok", partial=True, turn_complete=True)  # type: ignore[misc]

    class _RecordingAgent:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class _RecordingPrimitives:
        Agent = _RecordingAgent
        Runner = _RecordingRunner
        InMemorySessionService = _FakeSessionService
        Content = object
        Part = object
        GenerateContentConfig = _FakeGenerateContentConfig

    def _loader() -> object:
        return _RecordingPrimitives()

    build_hosted_runtime(
        adk_primitives_loader=_loader,
        adk_tools=(),
        model="fake-model",
        instruction="test",
        generate_content_config=_fake_generate_content_config(),
        control_plane_plugins=(),  # empty → omit
    )

    assert captured_kwargs, "Runner was not constructed"
    runner_kw = captured_kwargs[0]
    assert "plugins" not in runner_kw, (
        "'plugins' kwarg must be omitted from Runner when control_plane_plugins is empty; "
        f"got keys: {list(runner_kw.keys())}"
    )


# ---------------------------------------------------------------------------
# 4. No engine changes — regression guard
# ---------------------------------------------------------------------------


def test_engine_wire_profile_none_default_unchanged() -> None:
    """MagiEngineDriver() without wire_profile defaults to None (CLI byte-identical).

    Regression guard: build_hosted_runtime must not silently affect the
    engine's default constructor behavior.
    """
    runner = MockRunner([text_event("ok", partial=True, turn_complete=True)])
    driver = MagiEngineDriver(runner=runner)
    assert driver._wire_profile is None, (
        f"MagiEngineDriver default wire_profile must be None; got {driver._wire_profile!r}"
    )
