from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.shadow.ts_parity_replay import (
    TsParityReplayFixture,
    load_ts_parity_replay_fixture,
    replay_ts_parity_fixture,
)


FIXTURES = Path(__file__).parent / "fixtures" / "ts_parity_replay"


def test_ts_parity_fixture_preserves_transcript_control_sse_tool_memory_and_compaction() -> None:
    fixture = load_ts_parity_replay_fixture(
        "representative_turn.json",
        fixture_root=FIXTURES,
    )

    replay = replay_ts_parity_fixture(fixture)

    assert replay.fixture_id == "ts_parity_fixture_0001"
    assert replay.local_diagnostic is True
    assert replay.transcript_kinds == (
        "turn_started",
        "user_message",
        "tool_call",
        "tool_result",
        "assistant_text",
        "turn_committed",
        "compaction_boundary",
        "control_event",
        "control_event",
    )
    assert replay.tool_links == {"toolu_read_1": ("tool_call", "tool_result")}
    assert replay.control_lifecycle == {"req-1": ("created", "approved")}
    assert replay.memory_modes == ("normal", "read_only", "incognito")
    assert replay.source_authority_policy == "current_turn_over_memory"
    assert replay.compaction_boundary_ids == ("compact-1",)
    assert replay.no_false_memory_claims is True
    assert set(replay.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert "Bearer unsafe-token" not in replay.sse_body
    assert "sk-unsafe-secret" not in replay.sse_body
    assert "[redacted]" in replay.sse_body


def test_ts_parity_fixture_replays_terminal_control_request_lifecycle_metadata() -> None:
    fixture = load_ts_parity_replay_fixture(
        "control_lifecycle_terminal_states.json",
        fixture_root=FIXTURES,
    )

    replay = replay_ts_parity_fixture(fixture)

    assert fixture.schema_version == "tsParityReplayFixture.v1"
    assert replay.local_diagnostic is True
    assert replay.transcript_kinds == (
        "turn_started",
        "user_message",
        "assistant_text",
        "turn_committed",
        "control_event",
        "control_event",
        "control_event",
        "control_event",
    )
    assert [event.type for event in fixture.control_events] == [
        "control_request_created",
        "control_request_cancelled",
        "control_request_created",
        "control_request_timed_out",
    ]
    assert replay.control_lifecycle == {
        "req-cancel-1": ("created", "cancelled"),
        "req-timeout-1": ("created", "timed_out"),
    }
    assert set(replay.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert replay.no_false_memory_claims is True

    payload = json.loads(
        (FIXTURES / "control_lifecycle_terminal_states.json").read_text(
            encoding="utf-8",
        )
    )
    payload_strings = _json_string_values(payload)
    assert not any("sk-" in value or "Bearer " in value for value in payload_strings)
    assert not any(
        marker in value
        for value in payload_strings
        for marker in (
            "/data/bots",
            "/workspace",
            "/var/lib/kubelet",
            "postgres://",
            "postgresql://",
            "supabase://",
            "s3://",
            "gs://",
            "infra/k8s",
            "deploy.sh",
        )
    )


def _json_string_values(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(item for nested in value for item in _json_string_values(nested))
    if isinstance(value, dict):
        return tuple(item for nested in value.values() for item in _json_string_values(nested))
    return ()


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload["memoryFences"][0].update({"recallClaimed": True}),
            id="recall-claim",
        ),
        pytest.param(
            lambda payload: payload["memoryFences"][1].update({"writeClaimed": True}),
            id="write-claim",
        ),
        pytest.param(
            lambda payload: payload["memoryFences"][2].update({"providerCallMade": True}),
            id="provider-call",
        ),
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"adkRunnerInvoked": True}),
            id="runner-flag",
        ),
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"memoryProviderCalled": True}),
            id="memory-provider-flag",
        ),
    ),
)
def test_ts_parity_fixture_rejects_false_memory_claims_and_live_flags(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "representative_turn.json").read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises(ValidationError):
        TsParityReplayFixture.model_validate(payload)


def test_ts_parity_fixture_import_boundary_stays_runner_tool_and_memory_provider_free() -> None:
    code = """
import sys
import magi_agent.shadow.ts_parity_replay  # noqa: F401

forbidden = (
    'google.adk.runners',
    'magi_agent.adk_bridge.local_runner',
    'magi_agent.adk_bridge.runner_adapter',
    'magi_agent.tools.dispatcher',
    'magi_agent.tools.registry',
    'magi_agent.plugins.agentmemory',
    'magi_agent.memory',
    'magi_agent.app',
    'magi_agent.transport.chat',
)
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise AssertionError(f'forbidden modules loaded: {loaded}')
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
