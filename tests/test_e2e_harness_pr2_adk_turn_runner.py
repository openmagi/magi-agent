from __future__ import annotations

import asyncio
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.genai import types


PYTHON_ROOT = Path(__file__).resolve().parents[1]
LOCAL_ADK_TURN_RUNNER_ATTESTATION = "openmagi.local_adk_turn_runner.v1"


def _content(text: str = "hello") -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=text)])


def _request(**overrides: object):
    from magi_agent.runtime.adk_turn_runner import AdkTurnRequest

    payload = {
        "turnId": "turn-pr2",
        "userId": "user-pr2",
        "sessionId": "session-pr2",
        "invocationId": "invoke-pr2",
        "newMessage": _content(),
        "inputRefs": ("prompt:summary",),
        "evidenceRefs": ("evidence:local",),
        "recipeSnapshotId": "recipe:snapshot-pr2",
        "contextPlanDigest": "sha256:" + ("0" * 64),
    }
    payload.update(overrides)
    return AdkTurnRequest(**payload)


def _local_runner(runner: object):
    from magi_agent.runtime.adk_turn_runner import LocalAdkTurnRunnerBoundary

    return LocalAdkTurnRunnerBoundary.from_local_test_runner(runner)


def _fake_runner(
    events: list[object] | None = None,
    *,
    error: BaseException | None = None,
    wait_until_cancelled: bool = False,
):
    from magi_agent.runtime.adk_turn_runner import LocalAdkReplayRunner

    return LocalAdkReplayRunner(
        events=tuple(events or ()),
        error=error,
        wait_until_cancelled=wait_until_cancelled,
    )


def _assert_false_authority_and_writes(result: object) -> None:
    authority = result.authority.model_dump(by_alias=True)
    writes = result.production_writes.model_dump(by_alias=True)

    assert authority
    assert writes
    assert all(value is False for value in authority.values())
    assert all(value is False for value in writes.values())
    assert result.local_only is True
    assert result.user_visible_output is None


def test_default_config_is_disabled_and_does_not_invoke_fake_runner() -> None:
    from magi_agent.runtime.adk_turn_runner import AdkTurnRunner

    runner = _fake_runner(events=[{"type": "unused"}])

    result = asyncio.run(AdkTurnRunner().run_turn(_request(), runner=runner))

    assert result.status == "disabled"
    assert result.runner_invoked is False
    assert result.events == ()
    assert result.request_shape is None
    assert runner.calls == []
    _assert_false_authority_and_writes(result)


def test_enabled_fake_runner_success_records_request_shape_and_returns_events() -> None:
    from magi_agent.runtime.adk_turn_runner import (
        AdkTurnRunner,
        AdkTurnRunnerConfig,
    )

    events = [{"type": "model_event", "sequence": 1}]
    fake_runner = _fake_runner(events=events)

    result = asyncio.run(
        AdkTurnRunner().run_turn(
            _request(),
            runner=_local_runner(fake_runner),
            config=AdkTurnRunnerConfig(enabled=True, timeoutSeconds=0.5),
        )
    )

    assert result.status == "succeeded"
    assert result.runner_invoked is True
    assert result.events == tuple(events)
    assert result.event_count == 1
    assert result.error_category is None
    assert result.error_digest is None
    assert result.request_shape is not None
    assert result.request_shape["turnId"] == "turn-pr2"
    assert result.request_shape["phase"] == "planning"
    assert result.request_shape["provider"] == "google"
    assert result.request_shape["model"] == "gemini-3.5-flash"
    assert result.request_shape["modelTier"] == "cheap"
    assert result.request_shape["inputRefs"] == ["prompt:summary"]
    assert result.request_shape["evidenceRefs"] == ["evidence:local"]
    assert result.public_projection()["requestShape"] == result.request_shape
    _assert_false_authority_and_writes(result)


def test_fake_runner_exception_returns_failed_without_raw_sensitive_text() -> None:
    from magi_agent.runtime.adk_turn_runner import (
        AdkTurnRunner,
        AdkTurnRunnerConfig,
    )

    sensitive_text = "provider failed with sensitive marker at /Users/kevin/private"
    fake_runner = _fake_runner(error=RuntimeError(sensitive_text))

    result = asyncio.run(
        AdkTurnRunner().run_turn(
            _request(),
            runner=_local_runner(fake_runner),
            config=AdkTurnRunnerConfig(enabled=True, timeoutSeconds=0.5),
        )
    )

    public = json.dumps(result.public_projection(), sort_keys=True)
    result_text = str(result)

    assert result.status == "failed"
    assert result.runner_invoked is True
    assert result.events == ()
    assert result.error_category == "runner_exception"
    assert result.error_digest is not None
    assert "sha256:" in result.error_digest
    assert "sensitive marker" not in public
    assert "/Users/kevin/private" not in public
    assert "provider failed" not in public
    assert "sensitive marker" not in result_text
    assert "/Users/kevin/private" not in result_text
    assert "provider failed" not in result_text
    _assert_false_authority_and_writes(result)


def test_enabled_runner_rejects_missing_local_attestation_before_invocation() -> None:
    from magi_agent.runtime.adk_turn_runner import (
        AdkTurnRunner,
        AdkTurnRunnerConfig,
    )

    class UnattestedRunner:
        def __init__(self) -> None:
            self.openmagi_runner_attestation = LOCAL_ADK_TURN_RUNNER_ATTESTATION
            self.openmagi_local_only_runner = True
            self.openmagi_provider_attached = False
            self.openmagi_tool_execution_attached = False
            self.openmagi_traffic_attached = False
            self.calls: list[dict[str, object]] = []

        async def run_async(self, **kwargs: object):
            self.calls.append(kwargs)
            yield {"type": "must-not-run"}

    runner = UnattestedRunner()

    result = asyncio.run(
        AdkTurnRunner().run_turn(
            _request(),
            runner=runner,
            config=AdkTurnRunnerConfig(enabled=True, timeoutSeconds=0.5),
        )
    )

    assert result.status == "failed"
    assert result.runner_invoked is False
    assert result.error_category == "runner_local_attestation_missing"
    assert runner.calls == []
    _assert_false_authority_and_writes(result)


def test_local_runner_boundary_refuses_arbitrary_runner_objects() -> None:
    from magi_agent.runtime.adk_turn_runner import LocalAdkTurnRunnerBoundary

    class ArbitraryRunner:
        async def run_async(self, **_kwargs: object):
            yield {"type": "must-not-wrap"}

    with pytest.raises(ValueError, match="trusted local runner type"):
        LocalAdkTurnRunnerBoundary.from_local_test_runner(ArbitraryRunner())


def test_enabled_runner_rejects_boundary_subclass_spoof_before_invocation() -> None:
    from magi_agent.runtime.adk_turn_runner import LocalAdkTurnRunnerBoundary

    with pytest.raises(TypeError, match="does not support subclassing"):

        class EvilBoundary(LocalAdkTurnRunnerBoundary):
            pass


def test_local_runner_boundary_is_not_directly_invocable() -> None:
    boundary = _local_runner(_fake_runner(events=[{"type": "must-not-run"}]))

    assert not hasattr(boundary, "run_async")


def test_enabled_runner_rejects_mutated_boundary_runner_before_invocation() -> None:
    from magi_agent.runtime.adk_turn_runner import (
        AdkTurnRunner,
        AdkTurnRunnerConfig,
    )

    class ArbitraryRunner:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def run_async(self, **kwargs: object):
            self.calls.append(kwargs)
            yield {"type": "arbitrary-ran"}

    raw_runner = _fake_runner(events=[{"type": "must-not-run"}])
    boundary = _local_runner(raw_runner)
    arbitrary_runner = ArbitraryRunner()
    boundary._runner = arbitrary_runner

    result = asyncio.run(
        AdkTurnRunner().run_turn(
            _request(),
            runner=boundary,
            config=AdkTurnRunnerConfig(enabled=True, timeoutSeconds=0.5),
        )
    )

    assert result.status == "failed"
    assert result.runner_invoked is False
    assert result.error_category == "runner_local_attestation_missing"
    assert raw_runner.calls == []
    assert arbitrary_runner.calls == []
    _assert_false_authority_and_writes(result)


def test_enabled_runner_rejects_live_attached_runner_before_invocation() -> None:
    from magi_agent.runtime.adk_turn_runner import (
        AdkTurnRunner,
        AdkTurnRunnerConfig,
    )

    runner = _fake_runner(events=[{"type": "must-not-run"}])
    wrapped_runner = _local_runner(runner)
    runner.openmagi_provider_attached = True

    result = asyncio.run(
        AdkTurnRunner().run_turn(
            _request(),
            runner=wrapped_runner,
            config=AdkTurnRunnerConfig(enabled=True, timeoutSeconds=0.5),
        )
    )

    assert result.status == "failed"
    assert result.runner_invoked is False
    assert result.error_category == "runner_provider_attached"
    assert runner.calls == []
    _assert_false_authority_and_writes(result)


def test_unknown_but_safe_model_route_fails_before_runner_invocation() -> None:
    from magi_agent.runtime.adk_turn_runner import (
        AdkTurnRunner,
        AdkTurnRunnerConfig,
    )

    fake_runner = _fake_runner(events=[{"type": "must-not-run"}])

    result = asyncio.run(
        AdkTurnRunner().run_turn(
            _request(),
            runner=_local_runner(fake_runner),
            config=AdkTurnRunnerConfig.model_construct(
                enabled=True,
                provider="madeup",
                model="new-model",
                model_tier="standard",
                phase="planning",
                model_capabilities=(),
                timeout_seconds=0.5,
            ),
        )
    )

    assert result.status == "failed"
    assert result.runner_invoked is False
    assert result.error_category == "model_route_rejected"
    assert fake_runner.calls == []
    _assert_false_authority_and_writes(result)


def test_invalid_model_routing_config_fails_closed_before_runner_invocation() -> None:
    from magi_agent.runtime.adk_turn_runner import (
        AdkTurnRunner,
        AdkTurnRunnerConfig,
    )

    fake_runner = _fake_runner(events=[{"type": "must-not-run"}])

    result = asyncio.run(
        AdkTurnRunner().run_turn(
            _request(),
            runner=_local_runner(fake_runner),
            config=AdkTurnRunnerConfig.model_construct(
                enabled=True,
                provider="bad/provider",
                model="gemini-3.5-flash",
                model_tier="cheap",
                phase="planning",
                model_capabilities=(),
                timeout_seconds=0.5,
            ),
        )
    )

    public = json.dumps(result.public_projection(), sort_keys=True)
    assert result.status == "failed"
    assert result.runner_invoked is False
    assert result.error_category == "model_route_rejected"
    assert fake_runner.calls == []
    assert "bad/provider" not in public
    _assert_false_authority_and_writes(result)


def test_forbidden_adk_message_state_fails_closed_before_runner_invocation() -> None:
    from magi_agent.runtime.adk_turn_runner import (
        AdkTurnRunner,
        AdkTurnRunnerConfig,
    )

    fake_runner = _fake_runner(events=[{"type": "must-not-run"}])

    result = asyncio.run(
        AdkTurnRunner().run_turn(
            _request(
                newMessage=types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                name="leak",
                                args={"openmagi.currentTurnId": "must-not-pass"},
                            )
                        )
                    ],
                ),
            ),
            runner=_local_runner(fake_runner),
            config=AdkTurnRunnerConfig(enabled=True, timeoutSeconds=0.5),
        )
    )

    public = json.dumps(result.public_projection(), sort_keys=True)
    assert result.status == "failed"
    assert result.runner_invoked is False
    assert result.error_category == "runner_input_rejected"
    assert fake_runner.calls == []
    assert "openmagi.currentTurnId" not in public
    assert "must-not-pass" not in public
    _assert_false_authority_and_writes(result)


def test_spoofed_adk_message_object_is_not_dumped_before_real_validation() -> None:
    from magi_agent.runtime.adk_turn_runner import (
        AdkTurnRunner,
        AdkTurnRunnerConfig,
    )

    side_effects = {"count": 0}

    def model_dump(_self: object, **_kwargs: object) -> dict[str, object]:
        side_effects["count"] += 1
        return {"text": "must-not-dump"}

    def repr_spoof(_self: object) -> str:
        side_effects["count"] += 1
        return "must-not-repr"

    SpoofedContent = type(
        "Content",
        (),
        {
            "__module__": "google.genai.types",
            "model_dump": model_dump,
            "__repr__": repr_spoof,
        },
    )
    fake_runner = _fake_runner(events=[{"type": "must-not-run"}])

    result = asyncio.run(
        AdkTurnRunner().run_turn(
            _request(newMessage=SpoofedContent()),
            runner=_local_runner(fake_runner),
            config=AdkTurnRunnerConfig(enabled=True, timeoutSeconds=0.5),
        )
    )

    assert result.status == "failed"
    assert result.runner_invoked is False
    assert result.error_category == "runner_input_rejected"
    assert result.request_shape is None
    assert side_effects == {"count": 0}
    assert fake_runner.calls == []
    _assert_false_authority_and_writes(result)


def test_adk_content_subclass_is_rejected_before_model_dump_side_effect() -> None:
    from magi_agent.runtime.adk_turn_runner import (
        AdkTurnRunner,
        AdkTurnRunnerConfig,
    )

    side_effects = {"count": 0}

    class EvilContent(types.Content):
        def __repr__(self) -> str:
            side_effects["count"] += 1
            return "must-not-repr"

        def model_dump(self, **kwargs: object) -> dict[str, object]:
            side_effects["count"] += 1
            return super().model_dump(**kwargs)

    fake_runner = _fake_runner(events=[{"type": "must-not-run"}])

    result = asyncio.run(
        AdkTurnRunner().run_turn(
            _request(newMessage=EvilContent(role="user", parts=[types.Part(text="hi")])),
            runner=_local_runner(fake_runner),
            config=AdkTurnRunnerConfig(enabled=True, timeoutSeconds=0.5),
        )
    )

    assert result.status == "failed"
    assert result.runner_invoked is False
    assert result.error_category == "runner_input_rejected"
    assert result.request_shape is None
    assert side_effects == {"count": 0}
    assert fake_runner.calls == []
    _assert_false_authority_and_writes(result)


def test_run_turn_rejects_non_exact_request_before_attribute_access() -> None:
    from magi_agent.runtime.adk_turn_runner import (
        AdkTurnRunner,
        AdkTurnRunnerConfig,
    )

    side_effects = {"count": 0}

    class FakeRequest:
        @property
        def user_id(self) -> str:
            side_effects["count"] += 1
            return "must-not-read"

    fake_runner = _fake_runner(events=[{"type": "must-not-run"}])

    result = asyncio.run(
        AdkTurnRunner().run_turn(
            FakeRequest(),  # type: ignore[arg-type]
            runner=_local_runner(fake_runner),
            config=AdkTurnRunnerConfig(enabled=True, timeoutSeconds=0.5),
        )
    )

    assert result.status == "failed"
    assert result.runner_invoked is False
    assert result.error_category == "request_boundary_rejected"
    assert side_effects == {"count": 0}
    assert fake_runner.calls == []
    _assert_false_authority_and_writes(result)


def test_run_turn_rejects_non_exact_config_before_attribute_access() -> None:
    from magi_agent.runtime.adk_turn_runner import AdkTurnRunner

    side_effects = {"count": 0}

    class FakeConfig:
        @property
        def enabled(self) -> bool:
            side_effects["count"] += 1
            return True

    fake_runner = _fake_runner(events=[{"type": "must-not-run"}])

    result = asyncio.run(
        AdkTurnRunner().run_turn(
            _request(),
            runner=_local_runner(fake_runner),
            config=FakeConfig(),  # type: ignore[arg-type]
        )
    )

    assert result.status == "failed"
    assert result.runner_invoked is False
    assert result.error_category == "config_boundary_rejected"
    assert side_effects == {"count": 0}
    assert fake_runner.calls == []
    _assert_false_authority_and_writes(result)


def test_run_turn_rejects_constructed_request_before_ref_coercion() -> None:
    from magi_agent.runtime.adk_turn_runner import (
        AdkTurnRequest,
        AdkTurnRunner,
        AdkTurnRunnerConfig,
    )

    side_effects = {"count": 0}

    class EvilRef:
        def __str__(self) -> str:
            side_effects["count"] += 1
            return "auth:must-not-coerce"

    request = AdkTurnRequest.model_construct(
        turn_id="turn-pr2",
        user_id="user-pr2",
        session_id="session-pr2",
        invocation_id="invoke-pr2",
        new_message=_content(),
        input_refs=(EvilRef(),),
        evidence_refs=(),
        recipe_snapshot_id=None,
        context_plan_digest=None,
        harness_state={},
        state_delta={},
        run_config=None,
    )
    fake_runner = _fake_runner(events=[{"type": "must-not-run"}])

    result = asyncio.run(
        AdkTurnRunner().run_turn(
            request,
            runner=_local_runner(fake_runner),
            config=AdkTurnRunnerConfig(enabled=True, timeoutSeconds=0.5),
        )
    )

    assert result.status == "failed"
    assert result.runner_invoked is False
    assert result.error_category == "request_boundary_rejected"
    assert side_effects == {"count": 0}
    assert fake_runner.calls == []
    _assert_false_authority_and_writes(result)


def test_run_turn_rejects_constructed_config_before_truthiness() -> None:
    from magi_agent.runtime.adk_turn_runner import (
        AdkTurnRunner,
        AdkTurnRunnerConfig,
    )

    side_effects = {"count": 0}

    class EvilBool:
        def __bool__(self) -> bool:
            side_effects["count"] += 1
            return True

    config = AdkTurnRunnerConfig.model_construct(
        enabled=EvilBool(),
        timeout_seconds=0.5,
        provider="google",
        model="gemini-3.5-flash",
        model_tier="cheap",
        phase="planning",
        model_capabilities=(),
    )
    fake_runner = _fake_runner(events=[{"type": "must-not-run"}])

    result = asyncio.run(
        AdkTurnRunner().run_turn(
            _request(),
            runner=_local_runner(fake_runner),
            config=config,
        )
    )

    assert result.status == "failed"
    assert result.runner_invoked is False
    assert result.error_category == "config_boundary_rejected"
    assert side_effects == {"count": 0}
    assert fake_runner.calls == []
    _assert_false_authority_and_writes(result)


def test_run_turn_rejects_constructed_config_with_nonfinite_timeout() -> None:
    from magi_agent.runtime.adk_turn_runner import (
        AdkTurnRunner,
        AdkTurnRunnerConfig,
    )

    fake_runner = _fake_runner(events=[{"type": "must-not-run"}])

    for timeout in (math.nan, math.inf):
        result = asyncio.run(
            AdkTurnRunner().run_turn(
                _request(),
                runner=_local_runner(fake_runner),
                config=AdkTurnRunnerConfig.model_construct(
                    enabled=True,
                    timeout_seconds=timeout,
                    provider="google",
                    model="gemini-3.5-flash",
                    model_tier="cheap",
                    phase="planning",
                    model_capabilities=(),
                ),
            )
        )

        assert result.status == "failed"
        assert result.runner_invoked is False
        assert result.error_category == "config_boundary_rejected"
    assert fake_runner.calls == []


def test_run_turn_rejects_constructed_config_timeout_subclass_before_comparison() -> None:
    from magi_agent.runtime.adk_turn_runner import (
        AdkTurnRunner,
        AdkTurnRunnerConfig,
    )

    side_effects = {"count": 0}

    class EvilFloat(float):
        def __le__(self, _other: object) -> bool:
            side_effects["count"] += 1
            return False

    fake_runner = _fake_runner(events=[{"type": "must-not-run"}])

    result = asyncio.run(
        AdkTurnRunner().run_turn(
            _request(),
            runner=_local_runner(fake_runner),
            config=AdkTurnRunnerConfig.model_construct(
                enabled=True,
                timeout_seconds=EvilFloat(0.5),
                provider="google",
                model="gemini-3.5-flash",
                model_tier="cheap",
                phase="planning",
                model_capabilities=(),
            ),
        )
    )

    assert result.status == "failed"
    assert result.runner_invoked is False
    assert result.error_category == "config_boundary_rejected"
    assert side_effects == {"count": 0}
    assert fake_runner.calls == []


def test_config_rejects_enabled_and_timeout_coercion() -> None:
    from magi_agent.runtime.adk_turn_runner import AdkTurnRunnerConfig

    class EvilFloat(float):
        pass

    invalid_payloads = (
        {"enabled": "yes", "timeoutSeconds": 0.5},
        {"enabled": 1, "timeoutSeconds": 0.5},
        {"enabled": True, "timeoutSeconds": True},
        {"enabled": True, "timeoutSeconds": None},
        {"enabled": True, "timeoutSeconds": math.nan},
        {"enabled": True, "timeoutSeconds": math.inf},
        {"enabled": True, "timeoutSeconds": 0},
        {"enabled": True, "timeoutSeconds": -1},
        {"enabled": True, "timeoutSeconds": EvilFloat(0.5)},
    )

    for payload in invalid_payloads:
        with pytest.raises(ValueError):
            AdkTurnRunnerConfig(**payload)


def test_bool_timeout_normal_construction_cannot_invoke_runner() -> None:
    from magi_agent.runtime.adk_turn_runner import AdkTurnRunnerConfig

    fake_runner = _fake_runner(events=[{"type": "must-not-run"}])

    with pytest.raises(ValueError):
        AdkTurnRunnerConfig(enabled=True, timeoutSeconds=True)

    assert fake_runner.calls == []


def test_result_public_projection_sanitizes_constructed_request_shape() -> None:
    from magi_agent.runtime.adk_turn_runner import AdkTurnResult

    result = AdkTurnResult(
        status="failed",
        enabled=True,
        requestShape={
            "turnId": "turn-pr2",
            "inputDigest": "sha256:" + ("0" * 64),
            "inputRefs": [
                "safe:ref",
                "/Users/kevin/private",
                "auth:bearerless",
                "session:abc123",
                "rawToolArgs",
                "rawToolResult",
                "/home/kevin/.ssh/id_rsa",
                "raw-prompt",
                "raw prompt",
                "raw-tool-args",
                "raw.tool.result",
                "bearer:abc",
                "api-key",
            ],
            "validatorRefs": ["safe:validator", "rawPrompt", "raw-prompt"],
            "rawPrompt": "cookie token /Users/kevin/private",
            "validatorStatuses": {
                "safe:validator": "passed",
                "secret-token": "passed",
                "safe:bad": "unknown",
            },
        },
    )

    public = result.public_projection()
    dumped_result = result.model_dump(by_alias=True, mode="json")
    result_text = str(result)
    dumped = json.dumps(public, sort_keys=True)
    dumped_model = json.dumps(dumped_result, sort_keys=True)

    assert public["requestShape"] == {
        "inputDigest": "sha256:" + ("0" * 64),
        "inputRefs": ["safe:ref"],
        "turnId": "turn-pr2",
        "validatorRefs": ["safe:validator"],
        "validatorStatuses": {"safe:validator": "passed"},
    }
    assert "rawPrompt" not in dumped
    assert "rawToolArgs" not in dumped
    assert "rawToolResult" not in dumped
    assert "raw-prompt" not in dumped
    assert "raw prompt" not in dumped
    assert "raw-tool-args" not in dumped
    assert "raw.tool.result" not in dumped
    assert "bearer:abc" not in dumped
    assert "api-key" not in dumped
    assert "/home/kevin/.ssh/id_rsa" not in dumped
    assert "auth:bearerless" not in dumped
    assert "session:abc123" not in dumped
    assert "cookie" not in dumped
    assert "token" not in dumped
    assert "/Users/kevin/private" not in dumped
    assert "rawPrompt" not in dumped_model
    assert "rawToolArgs" not in dumped_model
    assert "rawToolResult" not in dumped_model
    assert "raw-prompt" not in dumped_model
    assert "raw prompt" not in dumped_model
    assert "raw-tool-args" not in dumped_model
    assert "raw.tool.result" not in dumped_model
    assert "bearer:abc" not in dumped_model
    assert "api-key" not in dumped_model
    assert "/home/kevin/.ssh/id_rsa" not in dumped_model
    assert "auth:bearerless" not in dumped_model
    assert "session:abc123" not in dumped_model
    assert "cookie" not in dumped_model
    assert "token" not in dumped_model
    assert "/Users/kevin/private" not in dumped_model
    assert "rawPrompt" not in result_text
    assert "rawToolArgs" not in result_text
    assert "rawToolResult" not in result_text
    assert "raw-prompt" not in result_text
    assert "raw prompt" not in result_text
    assert "raw-tool-args" not in result_text
    assert "raw.tool.result" not in result_text
    assert "bearer:abc" not in result_text
    assert "api-key" not in result_text
    assert "/home/kevin/.ssh/id_rsa" not in result_text
    assert "auth:bearerless" not in result_text
    assert "session:abc123" not in result_text
    assert "cookie" not in result_text
    assert "token" not in result_text
    assert "/Users/kevin/private" not in result_text


def test_result_model_construct_cannot_forge_local_or_user_visible_fields() -> None:
    from magi_agent.runtime.adk_turn_runner import AdkTurnResult

    result = AdkTurnResult.model_construct(
        status="failed",
        enabled=True,
        localOnly=False,
        userVisibleOutput="SECRET_VISIBLE",
        requestShape={"rawPrompt": "cookie token /Users/kevin/private"},
        authority={"userVisibleOutputAllowed": True},
        productionWrites={"dbWritten": True},
    )

    dumped_result = result.model_dump(by_alias=True, mode="json")
    result_text = str(result)

    assert dumped_result["localOnly"] is True
    assert dumped_result["userVisibleOutput"] is None
    assert all(value is False for value in dumped_result["authority"].values())
    assert all(value is False for value in dumped_result["productionWrites"].values())
    assert dumped_result["requestShape"] == {}
    assert "SECRET_VISIBLE" not in result_text
    assert "cookie" not in result_text
    assert "token" not in result_text
    assert "/Users/kevin/private" not in result_text


def test_run_turn_request_shape_sanitizes_raw_auth_session_and_tool_refs() -> None:
    from magi_agent.runtime.adk_turn_runner import (
        AdkTurnRunner,
        AdkTurnRunnerConfig,
    )

    fake_runner = _fake_runner(events=[{"type": "ok"}])

    result = asyncio.run(
        AdkTurnRunner().run_turn(
            _request(
                inputRefs=(
                    "safe:ref",
                    "auth:bearerless",
                    "session:abc123",
                    "rawToolArgs",
                    "rawToolResult",
                ),
                evidenceRefs=(
                    "evidence:safe",
                    "rawPrompt",
                    "auth:cookie",
                ),
            ),
            runner=_local_runner(fake_runner),
            config=AdkTurnRunnerConfig(enabled=True, timeoutSeconds=0.5),
        )
    )

    dumped = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    result_text = str(result)

    assert result.status == "succeeded"
    assert result.request_shape["inputRefs"] == ["safe:ref"]
    assert result.request_shape["evidenceRefs"] == ["evidence:safe"]
    for forbidden in (
        "auth:bearerless",
        "session:abc123",
        "rawToolArgs",
        "rawToolResult",
        "rawPrompt",
        "auth:cookie",
    ):
        assert forbidden not in dumped
        assert forbidden not in result_text


def test_timeout_returns_timed_out_and_cancels_fake_runner() -> None:
    from magi_agent.runtime.adk_turn_runner import (
        AdkTurnRunner,
        AdkTurnRunnerConfig,
    )

    fake_runner = _fake_runner(wait_until_cancelled=True)

    result = asyncio.run(
        AdkTurnRunner().run_turn(
            _request(),
            runner=_local_runner(fake_runner),
            config=AdkTurnRunnerConfig(enabled=True, timeoutSeconds=0.01),
        )
    )

    assert result.status == "timed_out"
    assert result.runner_invoked is True
    assert result.error_category == "timeout"
    assert fake_runner.cancelled is True
    _assert_false_authority_and_writes(result)


def test_runner_cancellation_returns_cancelled_status() -> None:
    from magi_agent.runtime.adk_turn_runner import (
        AdkTurnRunner,
        AdkTurnRunnerConfig,
    )

    fake_runner = _fake_runner(error=asyncio.CancelledError())

    result = asyncio.run(
        AdkTurnRunner().run_turn(
            _request(),
            runner=_local_runner(fake_runner),
            config=AdkTurnRunnerConfig(enabled=True, timeoutSeconds=0.5),
        )
    )

    assert result.status == "cancelled"
    assert result.runner_invoked is True
    assert result.error_category == "cancelled"
    _assert_false_authority_and_writes(result)


def test_payload_cannot_forge_production_authority_or_adk_kwargs() -> None:
    from magi_agent.runtime.adk_turn_runner import (
        AdkTurnRunner,
        AdkTurnRunnerConfig,
    )

    fake_runner = _fake_runner(events=[{"type": "ok"}])
    request = _request(
        harnessState={
            "trafficAttached": True,
            "transcriptWriteAllowed": True,
        },
        stateDelta={
            "toolHostActive": True,
            "openmagi": {"runConfig": {"live": True}},
        },
        runConfig={
            "userVisibleOutputAllowed": True,
            "dbWriteAllowed": True,
        },
    )

    result = asyncio.run(
        AdkTurnRunner().run_turn(
            request,
            runner=_local_runner(fake_runner),
            config=AdkTurnRunnerConfig(enabled=True, timeoutSeconds=0.5),
        )
    )

    assert result.status == "succeeded"
    assert len(fake_runner.calls) == 1
    assert set(fake_runner.calls[0]) <= {
        "user_id",
        "session_id",
        "invocation_id",
        "new_message",
        "run_config",
    }
    assert {
        "user_id",
        "session_id",
        "invocation_id",
        "new_message",
    } <= set(fake_runner.calls[0])
    assert "harness_state" not in fake_runner.calls[0]
    assert "state_delta" not in fake_runner.calls[0]
    if "run_config" in fake_runner.calls[0]:
        assert isinstance(fake_runner.calls[0]["run_config"], RunConfig)
        assert fake_runner.calls[0]["run_config"].streaming_mode == StreamingMode.SSE
    _assert_false_authority_and_writes(result)


def test_public_projection_and_string_exclude_raw_user_text_and_private_material() -> None:
    from magi_agent.runtime.adk_turn_runner import (
        AdkTurnRunner,
        AdkTurnRunnerConfig,
    )

    raw_user_text = (
        "read /Users/kevin/private/session_key.txt with auth cookie token and "
        "tool args {'path': '/Users/kevin/private'}"
    )
    fake_runner = _fake_runner(
        events=[
            {
                "rawToolArgs": {"path": "/Users/kevin/private"},
                "rawToolResult": "cookie token session_key private_key",
            }
        ]
    )

    result = asyncio.run(
        AdkTurnRunner().run_turn(
            _request(
                newMessage=_content(raw_user_text),
                inputRefs=(
                    "safe:ref",
                    "/Users/kevin/private",
                    "auth-token-secret",
                ),
                evidenceRefs=("evidence:safe", "cookie"),
            ),
            runner=_local_runner(fake_runner),
            config=AdkTurnRunnerConfig(enabled=True, timeoutSeconds=0.5),
        )
    )

    public = json.dumps(result.public_projection(), sort_keys=True)
    public_model_dump = json.dumps(
        result.model_dump(by_alias=True, mode="json"),
        sort_keys=True,
    )
    result_text = str(result)

    assert result.status == "succeeded"
    assert result.request_shape["inputRefs"] == ["safe:ref"]
    assert result.request_shape["evidenceRefs"] == ["evidence:safe"]
    for forbidden in (
        raw_user_text,
        "/Users/kevin/private",
        "auth cookie token",
        "rawToolArgs",
        "rawToolResult",
        "session_key",
        "private_key",
    ):
        assert forbidden not in public
        assert forbidden not in public_model_dump
        assert forbidden not in result_text
    _assert_false_authority_and_writes(result)


def test_import_boundary_does_not_import_live_runtime_modules_or_construct_adk_runner() -> None:
    script = """
import json
import sys

import magi_agent.runtime.adk_turn_runner  # noqa: F401

forbidden = [
    "google.genai",
    "google.genai.client",
    "google.genai.live",
    "google.genai.models",
    "google.adk.runners",
    "google.adk.agents",
    "magi_agent.adk_bridge.local_runner",
    "magi_agent.chat_proxy",
    "magi_agent.supabase",
    "magi_agent.deploy",
    "magi_agent.k8s",
    "magi_agent.frontend",
]
print(json.dumps({name: name in sys.modules for name in forbidden}, sort_keys=True))
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PYTHON_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    imported = json.loads(completed.stdout)
    assert imported == {name: False for name in imported}
