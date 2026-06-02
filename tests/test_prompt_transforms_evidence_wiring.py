"""PR3: integration of built-in prompt transforms + PromptTransform evidence.

Covers:
  * Registering a built-in transform on a HookBus and confirming the assembled
    prompt gains the transform's section (flag on) while protected blocks
    survive.
  * The optional ``evidence_sink`` wiring on ``build_system_prompt`` /
    ``build_system_prompt_blocks``: when a replacing hook runs and a sink is
    supplied, a ``PromptTransform`` payload is emitted; with the flag off or no
    sink, nothing is emitted.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from types import ModuleType

import pytest

from openmagi_core_agent.harness.resolved import build_default_resolved_harness_state
from openmagi_core_agent.hooks.builtin.prompt_transforms import (
    language_preference_transform,
    language_preference_transform_manifest,
    model_capability_transform,
    model_capability_transform_manifest,
)
from openmagi_core_agent.hooks.bus import HookBus, RegisteredHook
from openmagi_core_agent.hooks.context import HookContext


def _builder() -> ModuleType:
    return importlib.import_module("openmagi_core_agent.runtime.message_builder")


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


_IDENTITY = {"identity": "identity body"}
_NOW = _utc("2026-05-28T12:00:00Z")


def _build(builder: ModuleType, **overrides):
    kwargs = dict(
        session_key="sess-1",
        turn_id="turn-1",
        identity=_IDENTITY,
        now=_NOW,
    )
    kwargs.update(overrides)
    return builder.build_system_prompt(**kwargs)


def _enabled_manifest(factory):
    """A built-in manifest, force-enabled so the bus runs it under test."""
    return factory().model_copy(update={"enabled": True})


def _bus(manifest, handler) -> HookBus:
    return HookBus(hooks=(RegisteredHook(manifest=manifest, handler=handler),))


# ---------------------------------------------------------------------------
# Built-in transform integrated end-to-end
# ---------------------------------------------------------------------------


class TestBuiltinTransformIntegration:
    def test_language_preference_adds_section(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_PROMPT_TRANSFORM_HOOKS_ENABLED", "1")
        builder = _builder()
        bus = _bus(
            _enabled_manifest(language_preference_transform_manifest),
            language_preference_transform,
        )
        out = _build(
            builder,
            hook_bus=bus,
            harness_state=build_default_resolved_harness_state(),
            hook_context=HookContext(bot_id="b", locale="ko-KR"),
        )
        assert "Respond in Korean." in out
        # protected blocks survive
        assert builder.DEFERRAL_PREVENTION_BLOCK in out
        assert builder.OUTPUT_RULES_BLOCK in out
        assert builder.ACTION_SAFETY_BLOCK in out

    def test_model_capability_adds_section(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_PROMPT_TRANSFORM_HOOKS_ENABLED", "1")
        builder = _builder()
        bus = _bus(
            _enabled_manifest(model_capability_transform_manifest),
            model_capability_transform,
        )
        out = _build(
            builder,
            hook_bus=bus,
            harness_state=build_default_resolved_harness_state(),
            model="claude-opus-4-8",
            hook_context=HookContext(bot_id="b", agent_model="claude-opus-4-8"),
        )
        assert "extended thinking" in out.lower()


# ---------------------------------------------------------------------------
# Evidence sink wiring
# ---------------------------------------------------------------------------


class TestEvidenceSink:
    def test_emits_payload_on_replace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_PROMPT_TRANSFORM_HOOKS_ENABLED", "1")
        builder = _builder()
        captured: list[dict[str, object]] = []
        bus = _bus(
            _enabled_manifest(language_preference_transform_manifest),
            language_preference_transform,
        )
        _build(
            builder,
            hook_bus=bus,
            harness_state=build_default_resolved_harness_state(),
            hook_context=HookContext(bot_id="b", locale="ja"),
            evidence_sink=lambda payload: captured.append(dict(payload)),
        )
        assert len(captured) == 1
        payload = captured[0]
        assert payload["type"] == "PromptTransform"
        assert payload["sections_modified"] is True
        assert isinstance(payload["tokens_before"], int)
        assert isinstance(payload["tokens_after"], int)
        # adding a section can only grow (or hold) the token estimate
        assert payload["tokens_after"] >= payload["tokens_before"]
        assert "hook_name" in payload

    def test_sections_modified_false_when_no_replace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_PROMPT_TRANSFORM_HOOKS_ENABLED", "1")
        builder = _builder()
        captured: list[dict[str, object]] = []
        # locale absent -> transform returns continue -> no replace
        bus = _bus(
            _enabled_manifest(language_preference_transform_manifest),
            language_preference_transform,
        )
        _build(
            builder,
            hook_bus=bus,
            harness_state=build_default_resolved_harness_state(),
            hook_context=HookContext(bot_id="b"),  # no locale
            evidence_sink=lambda payload: captured.append(dict(payload)),
        )
        assert len(captured) == 1
        assert captured[0]["sections_modified"] is False
        assert captured[0]["tokens_before"] == captured[0]["tokens_after"]

    def test_no_sink_no_emission(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_PROMPT_TRANSFORM_HOOKS_ENABLED", "1")
        builder = _builder()
        # sink omitted entirely; must not raise and must transform normally
        bus = _bus(
            _enabled_manifest(language_preference_transform_manifest),
            language_preference_transform,
        )
        out = _build(
            builder,
            hook_bus=bus,
            harness_state=build_default_resolved_harness_state(),
            hook_context=HookContext(bot_id="b", locale="es"),
        )
        assert "Respond in Spanish." in out

    def test_flag_off_no_emission_even_with_sink(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MAGI_PROMPT_TRANSFORM_HOOKS_ENABLED", raising=False)
        builder = _builder()
        captured: list[dict[str, object]] = []
        golden = _build(builder)
        bus = _bus(
            _enabled_manifest(language_preference_transform_manifest),
            language_preference_transform,
        )
        out = _build(
            builder,
            hook_bus=bus,
            harness_state=build_default_resolved_harness_state(),
            hook_context=HookContext(bot_id="b", locale="ko"),
            evidence_sink=lambda payload: captured.append(dict(payload)),
        )
        # flag off => byte-identical + no emission
        assert out == golden
        assert captured == []

    def test_sink_error_does_not_break_assembly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_PROMPT_TRANSFORM_HOOKS_ENABLED", "1")
        builder = _builder()

        def boom(_payload):
            raise RuntimeError("sink exploded")

        bus = _bus(
            _enabled_manifest(language_preference_transform_manifest),
            language_preference_transform,
        )
        out = _build(
            builder,
            hook_bus=bus,
            harness_state=build_default_resolved_harness_state(),
            hook_context=HookContext(bot_id="b", locale="ko"),
            evidence_sink=boom,
        )
        # transform still applied despite sink failure
        assert "Respond in Korean." in out


# ---------------------------------------------------------------------------
# PromptTransform evidence record can be constructed from the payload
# ---------------------------------------------------------------------------


class TestEvidenceRecordConstruction:
    def test_payload_builds_a_valid_evidence_record(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openmagi_core_agent.evidence.types import EvidenceRecord, EvidenceSource

        monkeypatch.setenv("MAGI_PROMPT_TRANSFORM_HOOKS_ENABLED", "1")
        builder = _builder()
        captured: list[dict[str, object]] = []
        bus = _bus(
            _enabled_manifest(language_preference_transform_manifest),
            language_preference_transform,
        )
        _build(
            builder,
            hook_bus=bus,
            harness_state=build_default_resolved_harness_state(),
            hook_context=HookContext(bot_id="b", locale="ko"),
            evidence_sink=lambda payload: captured.append(dict(payload)),
        )
        payload = captured[0]
        record = EvidenceRecord(
            type="PromptTransform",
            status="ok",
            observedAt=0,
            source=EvidenceSource(kind="transcript"),
            fields={
                "hookName": list(payload["hook_name"]),
                "sectionsModified": payload["sections_modified"],
                "tokensBefore": payload["tokens_before"],
                "tokensAfter": payload["tokens_after"],
            },
        )
        assert record.type == "PromptTransform"
