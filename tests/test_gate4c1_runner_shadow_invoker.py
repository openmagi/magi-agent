from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from google.adk.events import Event
from google.genai import types
import pytest

import magi_agent.shadow.gate4c1_runner_shadow_invoker as runner_invoker_module
from magi_agent.shadow.gate4c0_shadow_config import (
    Gate4C0AllowlistMetadata,
    Gate4C0BudgetPolicy,
    Gate4C0InputEnvelopeMetadata,
    Gate4C0KillSwitchMetadata,
    Gate4C0MemoryPolicy,
    Gate4C0ModelRoutingMetadata,
    Gate4C0OutputIsolationPolicy,
    Gate4C0RecipeProfileMetadata,
    Gate4C0RedactionPolicy,
    Gate4C0ShadowConfig,
    Gate4C0ToolPolicy,
)
from magi_agent.shadow.gate4c1_runner_shadow_invoker import (
    Gate4C1AdkPrimitives,
    Gate4C1RunnerAuthorityFlags,
    Gate4C1RunnerShadowInvocationConfig,
    RunnerShadowInvoker,
    load_gate4c1_adk_primitives,
)


BOT_DIGEST = "sha256:" + "a" * 64
ORG_DIGEST = "sha256:" + "b" * 64
SESSION_DIGEST = "sha256:" + "c" * 64
BUNDLE_DIGEST = "sha256:" + "d" * 64
PROFILE_DIGEST = "sha256:" + "e" * 64


def _gate4c0_config(
    *,
    enabled: bool = True,
    kill_switch: bool = False,
    bot_allowlisted: bool = True,
    org_allowlisted: bool = True,
    environment_allowlisted: bool = True,
    tool_mode: str = "disabled",
) -> Gate4C0ShadowConfig:
    return Gate4C0ShadowConfig(
        enabled=enabled,
        allowlist=Gate4C0AllowlistMetadata(
            selectedBotDigest=BOT_DIGEST,
            selectedOrgDigest=ORG_DIGEST,
            environment="staging",
            botAllowlistDigests=(BOT_DIGEST,) if bot_allowlisted else ("sha256:" + "1" * 64,),
            orgAllowlistDigests=(ORG_DIGEST,) if org_allowlisted else ("sha256:" + "2" * 64,),
            environmentAllowlist=("staging",) if environment_allowlisted else ("development",),
        ),
        modelRouting=Gate4C0ModelRoutingMetadata(
            provider="google-adk",
            model="gemini-2.5-pro",
            modelProfile="production-default",
            routingProfileId="prod-routing-shadow-equivalent",
            credentialRef="shadow-provider-credential-ref",
            modelSelectionSource="bot_config_fallback",
        ),
        recipeProfile=Gate4C0RecipeProfileMetadata(
            recipeSnapshotId="recipe-snapshot:gate4c1",
            profileId="openmagi-opinionated",
            profileSnapshotDigest=PROFILE_DIGEST,
            selectedPackIds=("openmagi.research",),
        ),
        inputEnvelope=Gate4C0InputEnvelopeMetadata(
            source="gate4b_local_shadow_handoff",
            bundleIdDigest=BUNDLE_DIGEST,
            sessionIdDigest=SESSION_DIGEST,
            turnId="turn-20260518-0001",
            schemaVersion="gate4.localShadowHandoff.v1",
            redactionVerified=True,
            inputSizeBytes=128,
            eventCount=2,
        ),
        redactionPolicy=Gate4C0RedactionPolicy(maxInputBytes=8192, maxEventCount=16),
        toolPolicy=Gate4C0ToolPolicy(mode=tool_mode),
        memoryPolicy=Gate4C0MemoryPolicy(mode="read_only"),
        outputIsolation=Gate4C0OutputIsolationPolicy(),
        budget=Gate4C0BudgetPolicy(
            maxLatencyMs=30000,
            maxQueueDepth=25,
            maxDailyShadowRuns=100,
            maxCostUsd=1.25,
        ),
        killSwitch=Gate4C0KillSwitchMetadata(killSwitchEnabled=kill_switch),
    )


def _output_dir(tmp_path: Path) -> Path:
    path = tmp_path / "adk-shadow-capture" / "gate4c1"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _invocation_config(tmp_path: Path, **overrides: object) -> Gate4C1RunnerShadowInvocationConfig:
    base = {
        "enabled": True,
        "gate4c0Config": _gate4c0_config(),
        "sanitizedInputText": "Summarize the redacted duplicate turn in one sentence.",
        "outputDir": _output_dir(tmp_path),
        "maxInputChars": 512,
        "maxOutputChars": 80,
        "timeoutMs": 250,
    }
    base.update(overrides)
    return Gate4C1RunnerShadowInvocationConfig(**base)


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text

    @classmethod
    def from_text(cls, *, text: str) -> "_FakePart":
        return cls(text)


class _FakeContent:
    def __init__(self, *, parts: list[_FakePart], role: str | None = None) -> None:
        self.parts = parts
        self.role = role


class _FakeAgent:
    created_kwargs: dict[str, object] = {}

    def __init__(self, **kwargs: object) -> None:
        type(self).created_kwargs = kwargs


class _FakeSessionService:
    pass


class _FakeGenerateContentConfig:
    created_kwargs: dict[str, object] = {}

    def __init__(self, **kwargs: object) -> None:
        type(self).created_kwargs = kwargs


class _FakeEvent:
    def __init__(self, text: str) -> None:
        self.content = _FakeContent(parts=[_FakePart(text)], role="model")


class _FakeRunner:
    created_kwargs: dict[str, object] = {}
    run_kwargs: dict[str, object] = {}
    fail: bool = False

    def __init__(self, **kwargs: object) -> None:
        type(self).created_kwargs = kwargs

    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        if type(self).fail:
            raise RuntimeError("provider failure with Bearer unsafe-token")
        yield _FakeEvent(
            "Shadow preview with Authorization: Bearer unsafe-token and useful output.",
        )
        yield _FakeEvent("Final local-only shadow answer.")


def _fake_primitives() -> Gate4C1AdkPrimitives:
    _FakeAgent.created_kwargs = {}
    _FakeRunner.created_kwargs = {}
    _FakeRunner.run_kwargs = {}
    _FakeRunner.fail = False
    _FakeGenerateContentConfig.created_kwargs = {}
    return Gate4C1AdkPrimitives(
        Agent=_FakeAgent,
        Runner=_FakeRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


def _loader_that_must_not_run() -> Gate4C1AdkPrimitives:
    raise AssertionError("ADK primitives must not load for skipped or dropped invocations")


def test_gate4c1_runner_uses_official_adk_primitives_available_locally() -> None:
    primitives = load_gate4c1_adk_primitives()

    assert primitives.Agent.__module__.startswith("google.adk.")
    assert primitives.Runner.__module__.startswith("google.adk.")
    assert primitives.InMemorySessionService.__module__.startswith("google.adk.")


def test_gate4c1_runner_is_default_off_without_loading_adk_or_touching_paths() -> None:
    result = RunnerShadowInvoker(_loader_that_must_not_run).invoke(
        Gate4C1RunnerShadowInvocationConfig(
            enabled=False,
            gate4c0Config=_gate4c0_config(),
            sanitizedInputText="Safe redacted input.",
            outputDir=Path("/workspace/adk-shadow-capture"),
        )
    )

    assert result.status == "skipped"
    assert result.reason == "runner_disabled"
    assert result.runner_invoked is False
    assert result.diagnostic_artifact_path is None


def test_gate4c1_runner_skips_when_kill_switch_is_on(tmp_path: Path) -> None:
    result = RunnerShadowInvoker(_loader_that_must_not_run).invoke(
        _invocation_config(
            tmp_path,
            gate4c0Config=_gate4c0_config(kill_switch=True),
        )
    )

    assert result.status == "skipped"
    assert result.reason == "gate4c0_not_accepted"
    assert result.gate4c0_reason == "kill_switch_enabled"
    assert result.runner_invoked is False
    assert result.model_call_via_adk_runner_attempted is False
    assert result.attachment_flags.user_visible_output_attached is False


@pytest.mark.parametrize(
    ("config", "gate4c0_reason"),
    (
        (_gate4c0_config(bot_allowlisted=False), "bot_not_allowlisted"),
        (_gate4c0_config(org_allowlisted=False), "org_not_allowlisted"),
        (_gate4c0_config(environment_allowlisted=False), "environment_not_allowlisted"),
    ),
)
def test_gate4c1_runner_skips_when_allowlist_fails(
    tmp_path: Path,
    config: Gate4C0ShadowConfig,
    gate4c0_reason: str,
) -> None:
    result = RunnerShadowInvoker(_loader_that_must_not_run).invoke(
        _invocation_config(tmp_path, gate4c0Config=config)
    )

    assert result.status == "skipped"
    assert result.reason == "gate4c0_not_accepted"
    assert result.gate4c0_reason == gate4c0_reason
    assert result.runner_invoked is False


def test_gate4c1_runner_drops_redaction_failed_input_without_runner(tmp_path: Path) -> None:
    result = RunnerShadowInvoker(_loader_that_must_not_run).invoke(
        _invocation_config(
            tmp_path,
            sanitizedInputText="Authorization: Bearer unsafe-token must not reach ADK",
        )
    )

    assert result.status == "dropped"
    assert result.reason == "unsafe_input"
    assert result.runner_invoked is False
    assert result.model_call_via_adk_runner_attempted is False


def test_gate4c1_runner_drops_cookie_input_without_runner(tmp_path: Path) -> None:
    result = RunnerShadowInvoker(_loader_that_must_not_run).invoke(
        _invocation_config(
            tmp_path,
            sanitizedInputText="Cookie: sessionid=unsafe must not reach ADK",
        )
    )

    assert result.status == "dropped"
    assert result.reason == "unsafe_input"
    assert result.runner_invoked is False


def test_gate4c1_runner_drops_when_cost_budget_is_zero(tmp_path: Path) -> None:
    result = RunnerShadowInvoker(_loader_that_must_not_run).invoke(
        _invocation_config(
            tmp_path,
            gate4c0Config=_gate4c0_config().model_copy(
                update={
                    "budget": Gate4C0BudgetPolicy(
                        maxLatencyMs=30000,
                        maxQueueDepth=25,
                        maxDailyShadowRuns=100,
                        maxCostUsd=0,
                    )
                }
            ),
        )
    )

    assert result.status == "dropped"
    assert result.reason == "cost_budget_exhausted"
    assert result.runner_invoked is False
    assert result.model_call_via_adk_runner_attempted is False


def test_gate4c1_runner_drops_when_queue_budget_is_zero(tmp_path: Path) -> None:
    result = RunnerShadowInvoker(_loader_that_must_not_run).invoke(
        _invocation_config(
            tmp_path,
            gate4c0Config=_gate4c0_config().model_copy(
                update={
                    "budget": Gate4C0BudgetPolicy(
                        maxLatencyMs=30000,
                        maxQueueDepth=0,
                        maxDailyShadowRuns=100,
                        maxCostUsd=1.25,
                    )
                }
            ),
        )
    )

    assert result.status == "dropped"
    assert result.reason == "queue_budget_exhausted"
    assert result.runner_invoked is False


def test_gate4c1_runner_invalid_output_path_fails_open_without_runner() -> None:
    result = RunnerShadowInvoker(_loader_that_must_not_run).invoke(
        Gate4C1RunnerShadowInvocationConfig(
            enabled=True,
            gate4c0Config=_gate4c0_config(),
            sanitizedInputText="Safe redacted input.",
            outputDir=Path("/workspace/adk-shadow-capture/gate4c1"),
        )
    )

    assert result.status == "error"
    assert result.reason == "diagnostic_artifact_error"
    assert result.runner_invoked is False
    assert result.error_class == "Gate3BLocalConsumerError"


def test_gate4c1_runner_invokes_adk_with_allowlisted_kwargs_and_disabled_tools(
    tmp_path: Path,
) -> None:
    result = RunnerShadowInvoker(_fake_primitives).invoke(_invocation_config(tmp_path))
    payload = result.model_dump(by_alias=True, mode="json")

    assert result.status == "completed"
    assert result.reason == "runner_completed"
    assert result.runner_invoked is True
    assert result.model_call_via_adk_runner_attempted is True
    assert result.event_count == 2
    assert result.agent_kwargs_keys == (
        "description",
        "generate_content_config",
        "instruction",
        "model",
        "name",
        "tools",
    )
    assert result.runner_kwargs_keys == (
        "agent",
        "app_name",
        "auto_create_session",
        "session_service",
    )
    assert result.run_async_kwargs_keys == ("new_message", "session_id", "user_id")
    assert set(_FakeAgent.created_kwargs) == set(result.agent_kwargs_keys)
    assert _FakeAgent.created_kwargs["tools"] == []
    assert _FakeGenerateContentConfig.created_kwargs == {"maxOutputTokens": 80}
    assert set(_FakeRunner.created_kwargs) == set(result.runner_kwargs_keys)
    assert set(_FakeRunner.run_kwargs) == set(result.run_async_kwargs_keys)
    assert "state_delta" not in _FakeRunner.run_kwargs
    assert "run_config" not in _FakeRunner.run_kwargs
    assert payload["attachmentFlags"]["userVisibleOutputAttached"] is False
    assert payload["attachmentFlags"]["toolHostDispatched"] is False
    assert payload["attachmentFlags"]["memoryWritten"] is False
    assert payload["attachmentFlags"]["canaryRouted"] is False
    assert "unsafe-token" not in result.output_preview
    assert "Authorization:" not in result.output_preview
    assert "[REDACTED]" in result.output_preview
    assert result.diagnostic_artifact_path is not None
    assert result.diagnostic_artifact_path.is_file()
    assert "unsafe-token" not in result.diagnostic_artifact_path.read_text(encoding="utf-8")
    assert "Authorization:" not in result.diagnostic_artifact_path.read_text(encoding="utf-8")


def test_gate4c1_runner_projects_adk_events_into_local_diagnostic_compatibility_fields(
    tmp_path: Path,
) -> None:
    class AdkEventRunner(_FakeRunner):
        async def run_async(self, **kwargs: object) -> object:
            type(self).run_kwargs = kwargs
            yield Event(
                id="event-text",
                author="model",
                content=types.Content(
                    role="model",
                    parts=[types.Part(text="Projected ADK delta.")],
                ),
                partial=True,
                invocation_id="turn-20260518-0001",
            )
            yield Event(
                id="event-tool",
                author="model",
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                id="tool-adk-1",
                                name="ReadOnlyLookup",
                                args={"query": "adk parity"},
                            )
                        )
                    ],
                ),
                invocation_id="turn-20260518-0001",
                timestamp=1_779_000_123,
            )

    def adk_event_primitives() -> Gate4C1AdkPrimitives:
        primitives = _fake_primitives()
        return Gate4C1AdkPrimitives(
            Agent=primitives.Agent,
            Runner=AdkEventRunner,
            InMemorySessionService=primitives.InMemorySessionService,
            Content=primitives.Content,
            Part=primitives.Part,
            GenerateContentConfig=primitives.GenerateContentConfig,
        )

    result = RunnerShadowInvoker(adk_event_primitives).invoke(_invocation_config(tmp_path))

    assert result.status == "completed"
    assert result.output_preview == "Projected ADK delta."
    assert result.diagnostic_agent_events == (
        {"type": "text_delta", "delta": "Projected ADK delta."},
        {
            "type": "tool_start",
            "id": "tool-adk-1",
            "name": "ReadOnlyLookup",
            "input_preview": '{"query": "adk parity"}',
        },
    )
    assert result.diagnostic_legacy_deltas == ("Projected ADK delta.",)
    assert result.diagnostic_transcript_entries == (
        {
            "kind": "tool_call",
            "ts": 1_779_000_123,
            "turnId": "turn-20260518-0001",
            "toolUseId": "tool-adk-1",
            "name": "ReadOnlyLookup",
            "input": {"query": "adk parity"},
        },
    )
    artifact = result.diagnostic_artifact_path
    assert artifact is not None
    artifact_text = artifact.read_text(encoding="utf-8")
    assert "diagnosticAgentEvents" in artifact_text
    assert "productionSseWritten" in artifact_text


def test_gate4c1_runner_redacts_and_bounds_diagnostic_projection_fields(
    tmp_path: Path,
) -> None:
    class SecretAdkEventRunner(_FakeRunner):
        async def run_async(self, **kwargs: object) -> object:
            type(self).run_kwargs = kwargs
            yield Event(
                id="event-secret-text",
                author="model",
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            text="Bearer secret-token " + ("x" * 160),
                        )
                    ],
                ),
                partial=True,
                invocation_id="turn-20260518-0001",
            )
            yield Event(
                id="event-secret-tool",
                author="model",
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                id="tool-secret-1",
                                name="ReadOnlyLookup",
                                args={
                                    "authorization": "Bearer tool-secret",
                                    "api_key": "plain-fixture-key",
                                    "password": "hunter2",
                                    "nested": {
                                        "refresh_token": "nested-refresh-value",
                                        "safe": "visible summary",
                                    },
                                    "path": "/data/bots/bot/private.txt",
                                    "payload": "y" * 180,
                                },
                            )
                        )
                    ],
                ),
                invocation_id="turn-20260518-0001",
                timestamp=1_779_000_124,
            )

    def secret_adk_event_primitives() -> Gate4C1AdkPrimitives:
        primitives = _fake_primitives()
        return Gate4C1AdkPrimitives(
            Agent=primitives.Agent,
            Runner=SecretAdkEventRunner,
            InMemorySessionService=primitives.InMemorySessionService,
            Content=primitives.Content,
            Part=primitives.Part,
            GenerateContentConfig=primitives.GenerateContentConfig,
        )

    result = RunnerShadowInvoker(secret_adk_event_primitives).invoke(
        _invocation_config(tmp_path, maxOutputChars=64)
    )

    rendered = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert "secret-token" not in rendered
    assert "Bearer tool-secret" not in rendered
    assert "/data/bots" not in rendered
    assert "[REDACTED]" in rendered
    assert len(result.diagnostic_legacy_deltas[0]) <= 64
    assert len(result.diagnostic_agent_events[1]["input_preview"]) <= 64
    assert len(result.diagnostic_transcript_entries[0]["input"]["payload"]) <= 64
    assert result.diagnostic_transcript_entries[0]["input"]["authorization"] == "[REDACTED]"
    assert result.diagnostic_transcript_entries[0]["input"]["api_key"] == "[REDACTED]"
    assert result.diagnostic_transcript_entries[0]["input"]["password"] == "[REDACTED]"
    assert result.diagnostic_transcript_entries[0]["input"]["nested"]["refresh_token"] == "[REDACTED]"
    assert result.diagnostic_transcript_entries[0]["input"]["nested"]["safe"] == "visible summary"
    assert "plain-fixture-key" not in rendered
    assert "hunter2" not in rendered
    assert "nested-refresh-value" not in rendered


def test_gate4c1_runner_cookie_output_is_redacted(tmp_path: Path) -> None:
    class CookieEventRunner(_FakeRunner):
        async def run_async(self, **kwargs: object) -> object:
            type(self).run_kwargs = kwargs
            yield _FakeEvent("Set-Cookie: sessionid=unsafe; HttpOnly")

    def cookie_primitives() -> Gate4C1AdkPrimitives:
        primitives = _fake_primitives()
        return Gate4C1AdkPrimitives(
            Agent=primitives.Agent,
            Runner=CookieEventRunner,
            InMemorySessionService=primitives.InMemorySessionService,
            Content=primitives.Content,
            Part=primitives.Part,
            GenerateContentConfig=primitives.GenerateContentConfig,
        )

    result = RunnerShadowInvoker(cookie_primitives).invoke(_invocation_config(tmp_path))

    assert result.status == "completed"
    assert "Set-Cookie" not in result.output_preview
    assert "sessionid=unsafe" not in result.output_preview
    assert result.output_preview == "[REDACTED]"
    assert result.output_redaction_applied is True


def test_gate4c1_runner_slack_token_output_is_redacted(tmp_path: Path) -> None:
    class SlackEventRunner(_FakeRunner):
        async def run_async(self, **kwargs: object) -> object:
            type(self).run_kwargs = kwargs
            yield _FakeEvent("xoxc-1234567890-unsafe")

    def slack_primitives() -> Gate4C1AdkPrimitives:
        primitives = _fake_primitives()
        return Gate4C1AdkPrimitives(
            Agent=primitives.Agent,
            Runner=SlackEventRunner,
            InMemorySessionService=primitives.InMemorySessionService,
            Content=primitives.Content,
            Part=primitives.Part,
            GenerateContentConfig=primitives.GenerateContentConfig,
        )

    result = RunnerShadowInvoker(slack_primitives).invoke(_invocation_config(tmp_path))

    assert result.status == "completed"
    assert "xoxc" not in result.output_preview
    assert result.output_preview == "[REDACTED]"
    assert result.output_redaction_applied is True


def test_gate4c1_runner_artifact_write_failure_fails_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_write(path: Path, result: object) -> Path:
        raise OSError("disk full with Bearer unsafe-token")

    monkeypatch.setattr(runner_invoker_module, "_write_diagnostic_artifact", fail_write)

    result = RunnerShadowInvoker(_fake_primitives).invoke(_invocation_config(tmp_path))

    assert result.status == "error"
    assert result.reason == "diagnostic_artifact_error"
    assert result.runner_invoked is True
    assert result.fail_open is True
    assert result.diagnostic_artifact_path is None
    assert result.diagnostic_artifact_error_class == "OSError"
    assert "unsafe-token" not in (result.diagnostic_artifact_error_preview or "")


def test_gate4c1_runner_rejects_symlinked_nested_artifact_directory(
    tmp_path: Path,
) -> None:
    output_dir = _output_dir(tmp_path)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (output_dir / "runner-shadow").symlink_to(outside_dir, target_is_directory=True)

    result = RunnerShadowInvoker(_fake_primitives).invoke(_invocation_config(tmp_path))

    assert result.status == "error"
    assert result.reason == "diagnostic_artifact_error"
    assert result.diagnostic_artifact_path is None
    assert not (outside_dir / "gate4c1-runner-shadow-invocation.json").exists()


def test_gate4c1_runner_rejects_symlinked_temp_artifact_file(tmp_path: Path) -> None:
    output_dir = _output_dir(tmp_path)
    runner_dir = output_dir / "runner-shadow"
    runner_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "escaped.json"
    (runner_dir / ".gate4c1-runner-shadow-invocation.json.tmp").symlink_to(outside_file)

    result = RunnerShadowInvoker(_fake_primitives).invoke(_invocation_config(tmp_path))

    assert result.status == "error"
    assert result.reason == "diagnostic_artifact_error"
    assert result.diagnostic_artifact_path is None
    assert not outside_file.exists()


def test_gate4c1_runner_errors_fail_open_into_local_diagnostics(tmp_path: Path) -> None:
    def failing_primitives() -> Gate4C1AdkPrimitives:
        primitives = _fake_primitives()
        _FakeRunner.fail = True
        return primitives

    result = RunnerShadowInvoker(failing_primitives).invoke(_invocation_config(tmp_path))

    assert result.status == "error"
    assert result.reason == "runner_error"
    assert result.runner_invoked is True
    assert result.fail_open is True
    assert result.error_class == "RuntimeError"
    assert "unsafe-token" not in (result.error_preview or "")
    assert result.attachment_flags.production_transcript_written is False
    assert result.attachment_flags.production_sse_written is False
    assert result.attachment_flags.db_written is False
    assert result.attachment_flags.channel_delivered is False
    assert result.attachment_flags.workspace_mutated is False
    assert result.diagnostic_artifact_path is not None
    assert result.diagnostic_artifact_path.is_file()


def test_gate4c1_runner_output_authority_flags_cannot_be_enabled_by_copy_or_construct() -> None:
    flags = Gate4C1RunnerAuthorityFlags()

    copied = flags.model_copy(
        update={
            "userVisibleOutputAttached": True,
            "productionTranscriptWritten": True,
            "productionSseWritten": True,
            "dbWritten": True,
            "channelDelivered": True,
            "workspaceMutated": True,
            "memoryWritten": True,
            "memoryProviderCalled": True,
            "toolHostDispatched": True,
            "liveToolsExecuted": True,
            "productionStorageWritten": True,
            "productionQueueEnqueued": True,
            "telegramAttached": True,
            "billingAuthMutated": True,
            "modelRoutingMutated": True,
            "canaryRouted": True,
        }
    )
    constructed = Gate4C1RunnerAuthorityFlags.model_construct(
        user_visible_output_attached=True,
        production_transcript_written=True,
        production_sse_written=True,
        db_written=True,
        channel_delivered=True,
        workspace_mutated=True,
        memory_written=True,
        memory_provider_called=True,
        toolhost_dispatched=True,
        live_tools_executed=True,
        production_storage_written=True,
        production_queue_enqueued=True,
        telegram_attached=True,
        billing_auth_mutated=True,
        model_routing_mutated=True,
        canary_routed=True,
    )

    for payload in (
        copied.model_dump(by_alias=True, mode="json"),
        constructed.model_dump(by_alias=True, mode="json"),
    ):
        assert all(value is False for value in payload.values())
