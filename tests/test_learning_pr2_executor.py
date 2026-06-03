"""PR2 — Learning reflection executor skeleton.

TDD test suite (written first).  Tests cover the six mandatory behaviours:

1. OFF → disabled no-op (zero work, empty candidates, no transcript reads).
2. ON + local-fake → deterministic candidates from injected fixtures.
3. Watermark advances / incremental (only traces after ``since``).
4. ``Literal[False]`` authority flags cannot be set True (frozen + validator).
5. ``TranscriptSource`` protocol is satisfied by ``LocalFakeTranscriptSource``.
6. Result model shape is stable (status/candidates/watermark/counters).
"""
from __future__ import annotations

import asyncio
import os

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Subject under test
# ---------------------------------------------------------------------------
from magi_agent.learning.candidates import LearningCandidate
from magi_agent.harness.learning_executor import (
    LearningReflectionConfig,
    LearningReflectionResult,
    _REFLECTION_ENV_VAR,
    _reflection_enabled,
    run_reflection,
)
from magi_agent.gates.learning_readiness import (
    LearningReadinessConfig,
    learning_readiness_health_metadata,
)
from magi_agent.learning.candidates import (
    LocalFakeTranscriptSource,
    SessionTrace,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trace(
    session_id: str = "sess-abc",
    ts: str = "2026-06-03T10:00:00Z",
    final_output: str = "done",
) -> SessionTrace:
    return SessionTrace(
        session_id=session_id,
        turns=({"role": "user", "text": "hello"}, {"role": "agent", "text": final_output}),
        final_output=final_output,
        ts=ts,
    )


# ---------------------------------------------------------------------------
# 1. Env gate — OFF path (default)
# ---------------------------------------------------------------------------


class TestReflectionDisabledByDefault:
    def test_env_gate_off_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_REFLECTION_ENV_VAR, raising=False)
        assert _reflection_enabled() is False

    def test_env_gate_on_with_truthy_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for val in ("1", "true", "yes", "on", "TRUE", "YES", "ON"):
            monkeypatch.setenv(_REFLECTION_ENV_VAR, val)
            assert _reflection_enabled() is True

    def test_env_gate_off_with_falsy_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for val in ("0", "false", "no", "off", "", "FALSE"):
            monkeypatch.setenv(_REFLECTION_ENV_VAR, val)
            assert _reflection_enabled() is False

    def test_disabled_returns_disabled_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(_REFLECTION_ENV_VAR, raising=False)
        source = LocalFakeTranscriptSource(traces=(_make_trace(),))
        result = asyncio.get_event_loop().run_until_complete(
            run_reflection(source=source)
        )
        assert result.status == "disabled"

    def test_disabled_returns_empty_candidates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(_REFLECTION_ENV_VAR, raising=False)
        source = LocalFakeTranscriptSource(traces=(_make_trace(),))
        result = asyncio.get_event_loop().run_until_complete(
            run_reflection(source=source)
        )
        assert result.candidates == ()

    def test_disabled_does_zero_work(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When OFF, transcript source must never be read."""
        monkeypatch.delenv(_REFLECTION_ENV_VAR, raising=False)
        read_calls: list[str] = []

        class TrackingSource(LocalFakeTranscriptSource):
            async def read_since(self, watermark: str | None) -> tuple[SessionTrace, ...]:
                read_calls.append("read")
                return await super().read_since(watermark)

        source = TrackingSource(traces=(_make_trace(),))
        asyncio.get_event_loop().run_until_complete(run_reflection(source=source))
        assert read_calls == [], "Transcript source was read despite gate being OFF"

    def test_disabled_counters_are_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(_REFLECTION_ENV_VAR, raising=False)
        source = LocalFakeTranscriptSource(traces=(_make_trace(),))
        result = asyncio.get_event_loop().run_until_complete(
            run_reflection(source=source)
        )
        assert result.counters["traces_read"] == 0
        assert result.counters["candidates_produced"] == 0


# ---------------------------------------------------------------------------
# 2. ON + local-fake → deterministic candidates
# ---------------------------------------------------------------------------


class TestReflectionEnabledLocalFake:
    def test_on_returns_ok_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
        source = LocalFakeTranscriptSource(traces=(_make_trace("s1"), _make_trace("s2")))
        result = asyncio.get_event_loop().run_until_complete(
            run_reflection(source=source, config=LearningReflectionConfig(enabled=True))
        )
        assert result.status == "ok"

    def test_on_produces_candidates_tuple(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
        source = LocalFakeTranscriptSource(traces=(_make_trace("s1"), _make_trace("s2")))
        result = asyncio.get_event_loop().run_until_complete(
            run_reflection(source=source, config=LearningReflectionConfig(enabled=True))
        )
        # At least zero — it's a tuple, not None
        assert isinstance(result.candidates, tuple)

    def test_on_deterministic_across_calls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Same input → same candidates every time (no LLM randomness)."""
        monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
        traces = (_make_trace("s1", final_output="output-A"),)
        source_a = LocalFakeTranscriptSource(traces=traces)
        source_b = LocalFakeTranscriptSource(traces=traces)

        result_a = asyncio.get_event_loop().run_until_complete(
            run_reflection(source=source_a, config=LearningReflectionConfig(enabled=True))
        )
        result_b = asyncio.get_event_loop().run_until_complete(
            run_reflection(source=source_b, config=LearningReflectionConfig(enabled=True))
        )
        assert result_a.candidates == result_b.candidates

    def test_candidate_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Each candidate carries the required fields from the spec."""
        monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
        source = LocalFakeTranscriptSource(traces=(_make_trace("s1"),))
        result = asyncio.get_event_loop().run_until_complete(
            run_reflection(source=source, config=LearningReflectionConfig(enabled=True))
        )
        for c in result.candidates:
            assert isinstance(c, LearningCandidate)
            # Required fields must be present and non-empty
            assert c.kind in ("rule", "example", "eval")
            assert c.content
            assert c.rationale
            assert c.provenance is not None
            assert c.source_signal_ref

    def test_counters_reflect_work_done(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
        traces = (_make_trace("s1"), _make_trace("s2"))
        source = LocalFakeTranscriptSource(traces=traces)
        result = asyncio.get_event_loop().run_until_complete(
            run_reflection(source=source, config=LearningReflectionConfig(enabled=True))
        )
        assert result.counters["traces_read"] == len(traces)
        assert result.counters["candidates_produced"] == len(result.candidates)

    def test_empty_source_returns_ok_with_no_candidates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
        source = LocalFakeTranscriptSource(traces=())
        result = asyncio.get_event_loop().run_until_complete(
            run_reflection(source=source, config=LearningReflectionConfig(enabled=True))
        )
        assert result.status == "ok"
        assert result.candidates == ()


# ---------------------------------------------------------------------------
# 3. Watermark / incremental window
# ---------------------------------------------------------------------------


class TestWatermarkBehavior:
    def test_watermark_advances_after_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
        traces = (_make_trace("s1", ts="2026-06-03T10:00:00Z"),)
        source = LocalFakeTranscriptSource(traces=traces)
        result = asyncio.get_event_loop().run_until_complete(
            run_reflection(source=source, config=LearningReflectionConfig(enabled=True))
        )
        # Watermark must be returned (not None) after a successful run with traces
        assert result.watermark is not None

    def test_watermark_none_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(_REFLECTION_ENV_VAR, raising=False)
        source = LocalFakeTranscriptSource(traces=(_make_trace(),))
        result = asyncio.get_event_loop().run_until_complete(
            run_reflection(source=source)
        )
        assert result.watermark is None

    def test_incremental_only_reads_after_since(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Traces with ts <= watermark must be excluded."""
        monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
        old = _make_trace("old", ts="2026-06-01T00:00:00Z")
        new = _make_trace("new", ts="2026-06-03T12:00:00Z")
        source = LocalFakeTranscriptSource(traces=(old, new))
        # First run — read all
        result1 = asyncio.get_event_loop().run_until_complete(
            run_reflection(source=source, config=LearningReflectionConfig(enabled=True))
        )
        assert result1.counters["traces_read"] == 2

        # Second run — pass watermark from first result; only "new" should be read
        source2 = LocalFakeTranscriptSource(traces=(old, new))
        result2 = asyncio.get_event_loop().run_until_complete(
            run_reflection(
                source=source2,
                since=result1.watermark,
                config=LearningReflectionConfig(enabled=True),
            )
        )
        # "old" trace ts is before the watermark produced by the first run
        assert result2.counters["traces_read"] <= 1

    def test_watermark_string_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
        source = LocalFakeTranscriptSource(
            traces=(_make_trace("s1", ts="2026-06-03T10:00:00Z"),)
        )
        result = asyncio.get_event_loop().run_until_complete(
            run_reflection(source=source, config=LearningReflectionConfig(enabled=True))
        )
        if result.watermark is not None:
            assert isinstance(result.watermark, str)


# ---------------------------------------------------------------------------
# 4. Literal[False] authority flags cannot be set True
# ---------------------------------------------------------------------------


class TestAuthorityFlagsLockedFalse:
    def test_llm_attached_coerced_false_even_when_supplied_true(self) -> None:
        """Forging a truthy value for llm_attached is coerced to False (not raised).

        This is the same defence used by WorkflowExecutorConfig: the
        field_validator coerces any truthy input to False.  The frozen model
        then prevents mutation after construction.
        """
        cfg = LearningReflectionConfig.model_validate({"llmAttached": True})
        assert cfg.llm_attached is False

    def test_production_write_coerced_false_even_when_supplied_true(self) -> None:
        cfg = LearningReflectionConfig.model_validate({"productionWriteEnabled": True})
        assert cfg.production_write_enabled is False

    def test_real_transcript_source_coerced_false_even_when_supplied_true(self) -> None:
        cfg = LearningReflectionConfig.model_validate(
            {"realTranscriptSourceAttached": True}
        )
        assert cfg.real_transcript_source_attached is False

    def test_config_is_frozen(self) -> None:
        cfg = LearningReflectionConfig()
        with pytest.raises((ValidationError, TypeError)):
            cfg.enabled = True  # type: ignore[misc]

    def test_default_authority_flags_are_false(self) -> None:
        cfg = LearningReflectionConfig()
        assert cfg.llm_attached is False
        assert cfg.production_write_enabled is False
        assert cfg.real_transcript_source_attached is False

    def test_authority_flags_remain_false_even_via_model_construct(self) -> None:
        """model_construct() bypass should still produce False for authority flags."""
        cfg = LearningReflectionConfig.model_construct(
            llm_attached=True,
            production_write_enabled=True,
            real_transcript_source_attached=True,
        )
        # model_construct bypasses validators — the Literal[False] type system
        # still keeps them False via serialization / the frozen defence.
        # Assert serialized form never leaks True.
        dumped = cfg.model_dump(by_alias=True)
        assert dumped.get("llmAttached") is False
        assert dumped.get("productionWriteEnabled") is False
        assert dumped.get("realTranscriptSourceAttached") is False

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            LearningReflectionConfig.model_validate({"unknownField": True})


# ---------------------------------------------------------------------------
# 5. TranscriptSource protocol / SessionTrace shape
# ---------------------------------------------------------------------------


class TestTranscriptSource:
    def test_local_fake_returns_all_traces_when_no_watermark(self) -> None:
        traces = (_make_trace("s1"), _make_trace("s2"))
        source = LocalFakeTranscriptSource(traces=traces)
        result = asyncio.get_event_loop().run_until_complete(
            source.read_since(None)
        )
        assert len(result) == 2

    def test_local_fake_filters_by_watermark(self) -> None:
        traces = (
            _make_trace("old", ts="2026-06-01T00:00:00Z"),
            _make_trace("new", ts="2026-06-03T12:00:00Z"),
        )
        source = LocalFakeTranscriptSource(traces=traces)
        result = asyncio.get_event_loop().run_until_complete(
            source.read_since("2026-06-02T00:00:00Z")
        )
        assert len(result) == 1
        assert result[0].session_id == "new"

    def test_session_trace_shape(self) -> None:
        trace = _make_trace("s1", ts="2026-06-03T10:00:00Z", final_output="x")
        assert trace.session_id == "s1"
        assert trace.ts == "2026-06-03T10:00:00Z"
        assert trace.final_output == "x"
        assert isinstance(trace.turns, tuple)

    def test_session_trace_is_frozen(self) -> None:
        trace = _make_trace()
        with pytest.raises((ValidationError, TypeError)):
            trace.session_id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 6. Result model shape
# ---------------------------------------------------------------------------


class TestResultModel:
    def test_result_is_frozen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_REFLECTION_ENV_VAR, raising=False)
        source = LocalFakeTranscriptSource(traces=())
        result = asyncio.get_event_loop().run_until_complete(
            run_reflection(source=source)
        )
        with pytest.raises((ValidationError, TypeError)):
            result.status = "ok"  # type: ignore[misc]

    def test_result_status_literal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_REFLECTION_ENV_VAR, raising=False)
        source = LocalFakeTranscriptSource(traces=())
        result = asyncio.get_event_loop().run_until_complete(
            run_reflection(source=source)
        )
        assert result.status in ("disabled", "ok", "error")

    def test_result_candidates_is_tuple(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_REFLECTION_ENV_VAR, raising=False)
        source = LocalFakeTranscriptSource(traces=())
        result = asyncio.get_event_loop().run_until_complete(
            run_reflection(source=source)
        )
        assert isinstance(result.candidates, tuple)

    def test_result_counters_has_required_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(_REFLECTION_ENV_VAR, raising=False)
        source = LocalFakeTranscriptSource(traces=())
        result = asyncio.get_event_loop().run_until_complete(
            run_reflection(source=source)
        )
        assert "traces_read" in result.counters
        assert "candidates_produced" in result.counters

    def test_result_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            LearningReflectionResult.model_validate(
                {
                    "status": "disabled",
                    "candidates": [],
                    "watermark": None,
                    "counters": {"traces_read": 0, "candidates_produced": 0},
                    "unknownField": True,
                }
            )


# ---------------------------------------------------------------------------
# 7. Learning readiness gate (gates/learning_readiness.py)
# ---------------------------------------------------------------------------


class TestLearningReadinessGate:
    def test_disabled_by_default(self) -> None:
        cfg = LearningReadinessConfig()
        meta = learning_readiness_health_metadata(cfg)
        assert meta["status"] == "disabled"
        assert meta["readinessReady"] is False

    def test_reflect_authority_locked_false(self) -> None:
        cfg = LearningReadinessConfig()
        assert cfg.reflect_authority is False

    def test_reflect_authority_coerced_false_even_when_supplied_true(self) -> None:
        """Forging a truthy reflectAuthority is silently coerced to False."""
        cfg = LearningReadinessConfig.model_validate({"reflectAuthority": True})
        assert cfg.reflect_authority is False

    def test_enabled_gate_returns_ready(self) -> None:
        cfg = LearningReadinessConfig(enabled=True, kill_switch_enabled=False)
        meta = learning_readiness_health_metadata(cfg)
        assert meta["readinessReady"] is True
        assert meta["status"] == "enabled"

    def test_health_metadata_shape(self) -> None:
        cfg = LearningReadinessConfig()
        meta = learning_readiness_health_metadata(cfg)
        for key in ("enabled", "status", "readinessReady", "reflectAuthority", "reasonCodes"):
            assert key in meta

    def test_reason_codes_is_list(self) -> None:
        cfg = LearningReadinessConfig()
        meta = learning_readiness_health_metadata(cfg)
        assert isinstance(meta["reasonCodes"], list)
