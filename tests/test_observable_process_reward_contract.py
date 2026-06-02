from __future__ import annotations

import math
from collections.abc import Callable

import pytest
from pydantic import BaseModel, ValidationError

from openmagi_core_agent.harness.process_reward import (
    OBSERVABLE_PROCESS_SIGNAL_CATALOG,
    ObservableProcessEvent,
    ObservableProcessScoredEvent,
    ProcessRewardAggregation,
    ProcessRewardThresholds,
    score_observable_process_events,
)


def _event(
    *,
    signal_kind: str = "read_before_write",
    polarity: str = "positive",
    run_on: str = "child",
    agent_role: str = "coding",
    spawn_depth: int = 1,
    source_surface: str = "tool_result",
    public_preview: str = "read file before editing",
    metadata: dict[str, object] | None = None,
) -> ObservableProcessEvent:
    return ObservableProcessEvent(
        signalKind=signal_kind,
        polarity=polarity,
        sessionId="session-1",
        turnId="turn-1",
        runOn=run_on,
        agentRole=agent_role,
        spawnDepth=spawn_depth,
        sourceSurface=source_surface,
        publicPreview=public_preview,
        metadata=metadata or {},
    )


def test_stable_builtin_signal_catalog_contains_all_signals_and_is_report_only() -> None:
    expected = {
        "read_before_write",
        "deterministic_exactness",
        "source_grounding",
        "verification_discipline",
        "loop_control",
        "parallelism",
        "delivery_reliability",
        "self_debugging",
    }

    assert set(OBSERVABLE_PROCESS_SIGNAL_CATALOG) == expected
    assert all(signal.default_report_only for signal in OBSERVABLE_PROCESS_SIGNAL_CATALOG.values())
    assert all(not signal.policy_attached for signal in OBSERVABLE_PROCESS_SIGNAL_CATALOG.values())
    assert all(signal.positive_weight > 0 for signal in OBSERVABLE_PROCESS_SIGNAL_CATALOG.values())
    assert all(signal.negative_weight > 0 for signal in OBSERVABLE_PROCESS_SIGNAL_CATALOG.values())


def test_score_uses_observable_event_polarities_and_weights_deterministically() -> None:
    events = (
        _event(signal_kind="read_before_write", polarity="positive"),
        _event(signal_kind="source_grounding", polarity="positive"),
        _event(signal_kind="verification_discipline", polarity="negative"),
        _event(signal_kind="parallelism", polarity="neutral"),
    )

    first = score_observable_process_events(events)
    second = score_observable_process_events(reversed(events))

    assert first.score == pytest.approx(2 / 3)
    assert first == second
    assert first.positive_weight == pytest.approx(2.0)
    assert first.negative_weight == pytest.approx(1.0)
    assert first.neutral_event_count == 1
    assert first.report_only is True
    assert first.policy_attached is False


@pytest.mark.parametrize("source_surface", ("hidden_reasoning", "chain_of_thought", "model_internal"))
def test_hidden_and_model_internal_source_surfaces_are_rejected(source_surface: str) -> None:
    with pytest.raises(ValidationError):
        _event(source_surface=source_surface)


def test_user_approved_shortcuts_neutralize_negative_penalties_and_preserve_reason() -> None:
    report = score_observable_process_events(
        (
            _event(
                signal_kind="verification_discipline",
                polarity="negative",
                metadata={
                    "userApprovedShortcut": {
                        "reason": "User explicitly said to skip tests for a copy-only answer."
                    }
                },
            ),
        )
    )

    assert report.score == 1.0
    assert report.negative_weight == 0.0
    assert report.shortcut_neutralized_count == 1
    assert report.events[0].effective_polarity == "neutral"
    assert report.events[0].shortcut_reason == "User explicitly said to skip tests for a copy-only answer."


@pytest.mark.parametrize("scope", ("turn", "task", "benchmark"))
def test_aggregation_supports_turn_task_and_benchmark_scopes_without_policy_attachment(
    scope: str,
) -> None:
    aggregation = ProcessRewardAggregation(scope=scope, taskId="task-1", benchmarkId="bench-1")
    report = score_observable_process_events((_event(),), aggregation=aggregation)

    assert report.aggregation.scope == scope
    assert report.aggregation.session_id == "session-1"
    assert report.aggregation.turn_ids == ("turn-1",)
    assert report.traffic_attached is False
    assert report.execution_attached is False
    assert report.runner_attached is False
    assert report.route_attached is False
    assert report.policy_attached is False
    assert report.canary_attached is False


def test_thresholds_are_represented_but_cannot_attach_routing_blocking_or_canary_policy() -> None:
    thresholds = ProcessRewardThresholds(warnBelow=0.7, failBelow=0.4)
    report = score_observable_process_events(
        (_event(signal_kind="verification_discipline", polarity="negative"),),
        thresholds=thresholds,
    )

    assert report.thresholds.warn_below == 0.7
    assert report.thresholds.fail_below == 0.4
    assert report.thresholds.policy_use_enabled is False
    assert report.thresholds.blocking_enabled is False
    assert report.thresholds.canary_attached is False
    assert report.threshold_status == "fail"

    with pytest.raises(ValidationError):
        ProcessRewardThresholds(policyUseEnabled=True)
    with pytest.raises(ValidationError):
        ProcessRewardThresholds(blockingEnabled=True)
    with pytest.raises(ValidationError):
        ProcessRewardThresholds(canaryAttached=True)


def test_public_previews_redact_secrets_and_truncate() -> None:
    event = _event(
        public_preview=(
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz "
            "OPENAI_API_KEY=sk-proj-secret "
            "GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz "
            "SERVICE_ROLE_KEY=super-secret\n"
            + ("visible " * 80)
        )
    )

    assert "Bearer [redacted]" in event.public_preview
    assert "sk-proj-secret" not in event.public_preview
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in event.public_preview
    assert "SERVICE_ROLE_KEY=[redacted]" in event.public_preview
    assert len(event.public_preview) <= 400
    assert event.public_preview.endswith("...")


def test_scored_event_public_preview_redacts_and_truncates_on_construction_and_copy() -> None:
    unsafe_preview = (
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz "
        "OPENAI_API_KEY=sk-proj-secret "
        + ("visible " * 80)
    )
    scored = ObservableProcessScoredEvent(
        signalKind="read_before_write",
        polarity="positive",
        effectivePolarity="positive",
        weight=1.0,
        sessionId="session-1",
        turnId="turn-1",
        runOn="main",
        agentRole="coding",
        spawnDepth=0,
        sourceSurface="transcript",
        publicPreview=unsafe_preview,
        metadata={},
    )

    assert "Bearer [redacted]" in scored.public_preview
    assert "sk-proj-secret" not in scored.public_preview
    assert len(scored.public_preview) <= 400

    copied = scored.model_copy(update={"publicPreview": unsafe_preview})

    assert "Bearer [redacted]" in copied.public_preview
    assert "sk-proj-secret" not in copied.public_preview
    assert len(copied.public_preview) <= 400


def test_main_child_agent_role_and_spawn_depth_metadata_is_preserved() -> None:
    main_event = _event(run_on="main", spawn_depth=0, agent_role="general")
    child_event = _event(run_on="child", spawn_depth=2, agent_role="research")

    assert main_event.run_on == "main"
    assert main_event.spawn_depth == 0
    assert main_event.agent_role == "general"
    assert child_event.run_on == "child"
    assert child_event.spawn_depth == 2
    assert child_event.agent_role == "research"


def test_model_copy_cannot_enable_attachment_flags_or_inject_non_json_metadata() -> None:
    event = _event()
    report = score_observable_process_events((event,))

    with pytest.raises(ValidationError):
        event.model_copy(update={"traffic_attached": True})
    with pytest.raises(ValidationError):
        event.model_copy(update={"metadata": {"bad": object()}})
    with pytest.raises(ValidationError):
        report.model_copy(update={"policy_attached": True})
    with pytest.raises(ValidationError):
        report.model_copy(update={"events": (report.events[0].model_copy(update={"metadata": {"bad": object()}}),)})


@pytest.mark.parametrize(
    ("factory", "alias_name"),
    (
        (lambda: _event(), "trafficAttached"),
        (lambda: _event(), "policyAttached"),
        (lambda: _event(), "canaryAttached"),
        (lambda: ProcessRewardThresholds(), "trafficAttached"),
        (lambda: ProcessRewardThresholds(), "policyAttached"),
        (lambda: ProcessRewardThresholds(), "canaryAttached"),
        (lambda: ProcessRewardAggregation(), "trafficAttached"),
        (lambda: ProcessRewardAggregation(), "policyAttached"),
        (lambda: ProcessRewardAggregation(), "canaryAttached"),
        (lambda: score_observable_process_events((_event(),)), "trafficAttached"),
        (lambda: score_observable_process_events((_event(),)), "policyAttached"),
        (lambda: score_observable_process_events((_event(),)), "canaryAttached"),
    ),
)
def test_model_copy_rejects_camel_case_attachment_alias_updates(
    factory: Callable[[], BaseModel],
    alias_name: str,
) -> None:
    model = factory()

    with pytest.raises(ValidationError):
        model.model_copy(update={alias_name: True})


@pytest.mark.parametrize(
    "metadata",
    (
        {"bad": math.inf},
        {"bad": math.nan},
        {"bad": b"bytes"},
        {"bad": ("tuple",)},
        {"bad": object()},
        {"nested": {1: "non-string key"}},
    ),
)
def test_metadata_rejects_non_json_like_values(metadata: dict[object, object]) -> None:
    with pytest.raises(ValidationError):
        _event(metadata=metadata)


def test_metadata_accepts_json_scalars_lists_and_dicts() -> None:
    event = _event(
        metadata={
            "none": None,
            "string": "value",
            "int": 1,
            "float": 1.25,
            "bool": True,
            "list": [None, "value", 1, 1.25, False, {"nested": "ok"}],
            "dict": {"nested": {"value": "ok"}},
        }
    )

    assert event.metadata["list"][-1] == {"nested": "ok"}

    dumped = event.model_dump(by_alias=True)
    assert dumped["metadata"]["list"][-1] == {"nested": "ok"}


def test_metadata_is_defensively_immutable_after_event_validation_and_reporting() -> None:
    event = _event(
        metadata={
            "list": [None, {"nested": "ok"}],
            "dict": {"nested": {"value": "ok"}},
        }
    )
    report = score_observable_process_events((event,))

    with pytest.raises(TypeError):
        event.metadata["new"] = "bad"
    with pytest.raises(TypeError):
        event.metadata["dict"]["nested"]["value"] = "bad"
    with pytest.raises(TypeError):
        event.metadata["list"].append("bad")
    with pytest.raises(TypeError):
        report.events[0].metadata["new"] = "bad"
    with pytest.raises(TypeError):
        report.events[0].metadata["dict"]["nested"]["value"] = "bad"
    with pytest.raises(TypeError):
        report.events[0].metadata["list"].append("bad")


def test_signal_catalog_is_read_only_and_scoring_is_not_externally_mutable() -> None:
    with pytest.raises(TypeError):
        OBSERVABLE_PROCESS_SIGNAL_CATALOG["read_before_write"] = OBSERVABLE_PROCESS_SIGNAL_CATALOG[
            "verification_discipline"
        ]

    first = score_observable_process_events((_event(signal_kind="read_before_write"),))
    second = score_observable_process_events((_event(signal_kind="read_before_write"),))

    assert first.positive_weight == pytest.approx(1.0)
    assert second.positive_weight == pytest.approx(1.0)


def test_alias_compatible_output_and_snake_case_input_with_extra_fields_forbidden() -> None:
    event = ObservableProcessEvent(
        signal_kind="read_before_write",
        polarity="positive",
        session_id="session-1",
        turn_id="turn-1",
        run_on="main",
        agent_role="coding",
        spawn_depth=0,
        source_surface="transcript",
        public_preview="ok",
        metadata={},
    )

    dumped = event.model_dump(by_alias=True)

    assert dumped["signalKind"] == "read_before_write"
    assert dumped["sessionId"] == "session-1"
    assert dumped["sourceSurface"] == "transcript"
    with pytest.raises(ValidationError):
        ObservableProcessEvent.model_validate({**dumped, "unexpected": True})
