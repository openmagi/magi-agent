from __future__ import annotations

import ast
import asyncio
import json
import re
from pathlib import Path

import pytest


def _is_runtime_ref(value: object, namespace: str) -> bool:
    return isinstance(value, str) and re.fullmatch(rf"{namespace}:[a-f0-9]{{16}}", value) is not None


def _request() -> object:
    from magi_agent.runtime.child_runner_boundary import ChildTaskRequest

    return ChildTaskRequest(
        parentExecutionId="parent-exec-1",
        turnId="turn-1",
        taskId="task-1",
        objective="Review the patch without exposing raw child logs.",
        role="reviewer",
        delivery="return",
    )


class FakeChildRunner:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls = 0

    async def run_child(self, request: object) -> dict[str, object]:
        self.calls += 1
        return {
            "childExecutionId": "child-exec-1",
            "status": "completed",
            "summary": (
                "Reviewer found no blockers.\n"
                "raw_child_transcript: /workspace/bot/private.txt\n"
                "/home/kevin/.ssh/id_rsa\n"
                "/var/lib/kubelet/pods/x\n"
                "chain_of_thought: hidden reasoning\n"
                "Authorization: Bearer unsafe-token"
            ),
            "evidenceRefs": (
                "evidence:child-review-1",
                "/Users/kevin/private/raw-evidence.json",
                "/home/kevin/private/raw-evidence.json",
                "raw child transcript",
            ),
            "artifactRefs": (
                "artifact:child-report-1",
                "s3://private-bucket/raw-child-log",
            ),
            "auditEventRefs": ("audit:child-run-local",),
            "rawTranscript": "raw child transcript with sk-child-secret",
            "toolLogs": "raw tool logs",
            "hiddenReasoning": "private reasoning",
        }


class ThrowingChildRunner(FakeChildRunner):
    async def run_child(self, request: object) -> dict[str, object]:
        self.calls += 1
        raise RuntimeError(
            "child failed with /workspace/private and sk-child-secret hidden_reasoning"
        )


class LiveChildRunner:
    """A REAL (model-backed) child runner — declares ``openmagi_live_provider``."""

    openmagi_live_provider = True

    def __init__(self) -> None:
        self.calls = 0

    async def run_child(self, request: object) -> dict[str, object]:
        self.calls += 1
        return {
            "childExecutionId": "child-exec-live-1",
            "status": "completed",
            "summary": (
                "Live child completed the task.\n"
                "raw_child_transcript: /workspace/bot/private.txt\n"
                "/home/kevin/.ssh/id_rsa\n"
                "/var/lib/kubelet/pods/x\n"
                "chain_of_thought: hidden reasoning\n"
                "Authorization: Bearer unsafe-token"
            ),
            "evidenceRefs": (
                "evidence:child-live-1",
                "/Users/kevin/private/raw-evidence.json",
                "raw child transcript",
            ),
            "artifactRefs": (
                "artifact:child-live-1",
                "s3://private-bucket/raw-child-log",
            ),
            "auditEventRefs": ("audit:child-live-1",),
            "rawTranscript": "raw child transcript with sk-child-secret",
            "toolLogs": "raw tool logs",
            "hiddenReasoning": "private reasoning",
        }


class ThrowingLiveChildRunner:
    """A REAL (model-backed) live child runner that always raises.

    Used to verify the LIVE error path: the boundary must catch the exception,
    sanitise the error message (no paths, no tokens), set status="error" with
    error_code="live_child_runner_error", and NEVER raise.
    """

    openmagi_live_provider = True

    def __init__(self) -> None:
        self.calls = 0

    async def run_child(self, request: object) -> dict[str, object]:
        self.calls += 1
        raise RuntimeError("boom /Users/kevin/secret sk-live-AAA")


def test_child_runner_boundary_defaults_off_and_never_calls_fake_runner() -> None:
    from magi_agent.runtime.child_runner_boundary import (
        ChildRunnerConfig,
        LocalChildRunnerBoundary,
    )

    fake = FakeChildRunner()
    boundary = LocalChildRunnerBoundary(ChildRunnerConfig(), child_runner=fake)

    result = asyncio.run(boundary.run(_request()))
    projection = result.public_projection()

    assert result.status == "disabled"
    assert result.error_code == "child_runner_disabled"
    assert fake.calls == 0
    assert projection["authorityFlags"]["realChildRunnerExecuted"] is False
    assert projection["diagnosticMetadata"]["productionChildExecutionEnabled"] is False
    assert projection["diagnosticMetadata"]["productionWritesEnabled"] is False


def test_child_runner_boundary_calls_only_local_fake_runner_when_explicitly_enabled() -> None:
    from magi_agent.runtime.child_runner_boundary import (
        ChildRunnerConfig,
        LocalChildRunnerBoundary,
    )

    fake = FakeChildRunner()
    boundary = LocalChildRunnerBoundary(
        ChildRunnerConfig(enabled=True, localFakeChildRunnerEnabled=True),
        child_runner=fake,
    )

    result = asyncio.run(boundary.run(_request()))
    projection = result.public_projection()
    encoded = json.dumps(projection, sort_keys=True)

    assert fake.calls == 1
    assert result.status == "ok"
    assert result.envelope is not None
    assert result.envelope.child_ref.startswith("child:")
    assert result.envelope.summary == "Reviewer found no blockers."
    assert _is_runtime_ref(projection["parentOutputRefs"][0], "child")
    assert _is_runtime_ref(projection["parentOutputRefs"][1], "evidence")
    assert _is_runtime_ref(projection["parentOutputRefs"][2], "artifact")
    assert _is_runtime_ref(projection["parentOutputRefs"][3], "audit")
    assert projection["parentOutputRefs"][1:] != [
        "evidence:child-review-1",
        "artifact:child-report-1",
        "audit:child-run-local",
    ]
    assert "raw_child_transcript" not in encoded
    assert "chain_of_thought" not in encoded
    assert "Authorization" not in encoded
    assert "unsafe-token" not in encoded
    assert "/workspace/bot" not in encoded
    assert "/Users/kevin" not in encoded
    assert "/home/kevin" not in encoded
    assert "/var/lib/kubelet" not in encoded
    assert "s3://private-bucket" not in encoded
    assert '"rawTranscript":' not in encoded
    assert "toolLogs" not in encoded
    assert '"hiddenReasoning":' not in encoded
    assert projection["authorityFlags"]["childRunnerAttached"] is False
    assert projection["authorityFlags"]["parentContextRawInjection"] is False


def test_child_runner_boundary_enabled_without_local_fake_runner_stays_disabled() -> None:
    from magi_agent.runtime.child_runner_boundary import (
        ChildRunnerConfig,
        LocalChildRunnerBoundary,
    )

    fake = FakeChildRunner()
    boundary = LocalChildRunnerBoundary(ChildRunnerConfig(enabled=True), child_runner=fake)

    result = asyncio.run(boundary.run(_request()))

    assert result.status == "disabled"
    assert result.error_code == "local_fake_child_runner_disabled"
    assert fake.calls == 0


def test_child_runner_boundary_rejects_unmarked_fake_runner() -> None:
    from magi_agent.runtime.child_runner_boundary import (
        ChildRunnerConfig,
        LocalChildRunnerBoundary,
    )

    class UnmarkedChildRunner(FakeChildRunner):
        openmagi_local_fake_provider = False

    fake = UnmarkedChildRunner()
    boundary = LocalChildRunnerBoundary(
        ChildRunnerConfig(enabled=True, localFakeChildRunnerEnabled=True),
        child_runner=fake,
    )

    result = asyncio.run(boundary.run(_request()))

    assert result.status == "blocked"
    assert result.error_code == "local_fake_child_runner_untrusted"
    assert fake.calls == 0


def test_child_runner_boundary_catches_fake_runner_errors_without_raw_leakage() -> None:
    from magi_agent.runtime.child_runner_boundary import (
        ChildRunnerConfig,
        LocalChildRunnerBoundary,
    )

    fake = ThrowingChildRunner()
    boundary = LocalChildRunnerBoundary(
        ChildRunnerConfig(enabled=True, localFakeChildRunnerEnabled=True),
        child_runner=fake,
    )

    result = asyncio.run(boundary.run(_request()))
    projection = result.public_projection()
    encoded = json.dumps(projection, sort_keys=True)
    raw_encoded = json.dumps(result.model_dump(by_alias=True), sort_keys=True)

    assert result.status == "error"
    assert result.error_code == "local_fake_child_runner_error"
    assert fake.calls == 1
    assert "/workspace/private" not in encoded
    assert "sk-child-secret" not in encoded
    assert "hidden_reasoning" not in encoded
    assert "/workspace/private" not in raw_encoded
    assert "sk-child-secret" not in raw_encoded
    assert "hidden_reasoning" not in raw_encoded
    assert projection["authorityFlags"]["realChildRunnerExecuted"] is False


def test_child_runner_boundary_catches_live_runner_errors_without_raw_leakage() -> None:
    """LIVE error path: boundary never raises, sanitises the raw exception text.

    Mirrors ``test_child_runner_boundary_catches_fake_runner_errors_without_raw_leakage``
    exactly — status=="error", error_code=="live_child_runner_error", the raw
    exception text (path + token) must NOT appear in the public projection OR
    the full model_dump, and the runner is called exactly once.
    """
    from magi_agent.runtime.child_runner_boundary import (
        ChildRunnerConfig,
        LocalChildRunnerBoundary,
    )

    live = ThrowingLiveChildRunner()
    boundary = LocalChildRunnerBoundary(
        ChildRunnerConfig(enabled=True, liveChildRunnerEnabled=True),
        child_runner=live,
    )

    result = asyncio.run(boundary.run(_request()))
    projection = result.public_projection()
    encoded = json.dumps(projection, sort_keys=True)
    raw_encoded = json.dumps(result.model_dump(by_alias=True), sort_keys=True)

    assert result.status == "error"
    assert result.error_code == "live_child_runner_error"
    assert live.calls == 1
    # Raw exception text (path + token) must not leak in either surface.
    assert "/Users/kevin/secret" not in encoded
    assert "sk-live-AAA" not in encoded
    assert "/Users/kevin/secret" not in raw_encoded
    assert "sk-live-AAA" not in raw_encoded
    # Diagnostic should record that the runner was called (attempt semantics).
    assert projection["diagnosticMetadata"]["liveChildRunnerCalled"] is True
    # Sealed authority flags must stay False on the error path.
    assert projection["authorityFlags"]["realChildRunnerExecuted"] is False
    for flag_value in projection["authorityFlags"].values():
        assert flag_value is False


def test_child_runner_boundary_runs_live_runner_when_live_gate_enabled() -> None:
    from magi_agent.runtime.child_runner_boundary import (
        ChildRunnerConfig,
        LocalChildRunnerBoundary,
    )

    live = LiveChildRunner()
    boundary = LocalChildRunnerBoundary(
        ChildRunnerConfig(enabled=True, liveChildRunnerEnabled=True),
        child_runner=live,
    )

    result = asyncio.run(boundary.run(_request()))
    projection = result.public_projection()
    encoded = json.dumps(projection, sort_keys=True)
    raw_encoded = json.dumps(result.model_dump(by_alias=True), sort_keys=True)

    assert live.calls == 1
    assert result.status == "ok"
    assert result.envelope is not None
    assert result.envelope.status == "completed"
    assert result.envelope.child_ref.startswith("child:")
    assert result.envelope.summary == "Live child completed the task."
    # Output sanitisation reuses the same envelope path as the fake runner.
    assert _is_runtime_ref(projection["parentOutputRefs"][0], "child")
    assert _is_runtime_ref(projection["parentOutputRefs"][1], "evidence")
    assert _is_runtime_ref(projection["parentOutputRefs"][2], "artifact")
    assert _is_runtime_ref(projection["parentOutputRefs"][3], "audit")
    for leak in (
        "raw_child_transcript",
        "chain_of_thought",
        "Authorization",
        "unsafe-token",
        "/workspace/bot",
        "/Users/kevin",
        "/home/kevin",
        "/var/lib/kubelet",
        "s3://private-bucket",
        '"rawTranscript":',
        "toolLogs",
        '"hiddenReasoning":',
        "sk-child-secret",
    ):
        assert leak not in encoded
        assert leak not in raw_encoded
    # Diagnostic (non-authority) signal that a live run happened.
    assert projection["diagnosticMetadata"]["liveChildRunnerCalled"] is True
    # Sealed authority flags stay False on the live path.
    for flag_value in projection["authorityFlags"].values():
        assert flag_value is False
    assert projection["authorityFlags"]["realChildRunnerExecuted"] is False
    assert projection["authorityFlags"]["productionAuthority"] is False
    assert projection["diagnosticMetadata"]["productionChildExecutionEnabled"] is False
    assert projection["diagnosticMetadata"]["productionWritesEnabled"] is False


def test_child_runner_boundary_blocks_live_runner_when_live_gate_disabled() -> None:
    from magi_agent.runtime.child_runner_boundary import (
        ChildRunnerConfig,
        LocalChildRunnerBoundary,
    )

    live = LiveChildRunner()
    # Default config: live gate OFF (even though local-fake gate is enabled, a
    # live-marked runner must NEVER be admitted via the fake path).
    boundary = LocalChildRunnerBoundary(
        ChildRunnerConfig(enabled=True, localFakeChildRunnerEnabled=True),
        child_runner=live,
    )

    result = asyncio.run(boundary.run(_request()))
    projection = result.public_projection()

    assert live.calls == 0
    assert result.status == "blocked"
    assert result.error_code == "live_child_runner_not_enabled"
    assert projection["diagnosticMetadata"]["liveChildRunnerCalled"] is False
    assert projection["authorityFlags"]["realChildRunnerExecuted"] is False


def test_child_runner_boundary_fake_path_unchanged_with_live_gate_off() -> None:
    from magi_agent.runtime.child_runner_boundary import (
        ChildRunnerConfig,
        LocalChildRunnerBoundary,
    )

    fake = FakeChildRunner()
    boundary = LocalChildRunnerBoundary(
        ChildRunnerConfig(enabled=True, localFakeChildRunnerEnabled=True),
        child_runner=fake,
    )

    result = asyncio.run(boundary.run(_request()))

    assert fake.calls == 1
    assert result.status == "ok"
    assert result.envelope is not None
    assert result.envelope.summary == "Reviewer found no blockers."
    # The fake never runs through the live diagnostic signal.
    projection = result.public_projection()
    assert projection["diagnosticMetadata"]["liveChildRunnerCalled"] is False
    assert projection["diagnosticMetadata"]["localFakeChildRunnerCalled"] is True


def test_child_runner_boundary_does_not_admit_fake_via_live_branch() -> None:
    from magi_agent.runtime.child_runner_boundary import (
        ChildRunnerConfig,
        LocalChildRunnerBoundary,
    )

    # A fake-marked runner while ONLY the live gate is on must not be run by the
    # live branch (it lacks openmagi_live_provider) and falls through to the fake
    # gate, which is off here → disabled, fake never called.
    fake = FakeChildRunner()
    boundary = LocalChildRunnerBoundary(
        ChildRunnerConfig(enabled=True, liveChildRunnerEnabled=True),
        child_runner=fake,
    )

    result = asyncio.run(boundary.run(_request()))

    assert fake.calls == 0
    assert result.status == "disabled"
    assert result.error_code == "local_fake_child_runner_disabled"


def test_child_runner_boundary_live_path_enforces_spawn_depth_cap() -> None:
    from magi_agent.runtime.child_runner_boundary import (
        ChildRunnerConfig,
        ChildTaskRequest,
        LocalChildRunnerBoundary,
    )

    live = LiveChildRunner()
    boundary = LocalChildRunnerBoundary(
        ChildRunnerConfig(enabled=True, liveChildRunnerEnabled=True, maxSpawnDepth=2),
        child_runner=live,
    )
    deep_request = ChildTaskRequest(
        parentExecutionId="parent-exec-1",
        turnId="turn-1",
        taskId="task-1",
        objective="Run a deeply nested child turn.",
        role="reviewer",
        delivery="return",
        metadata={"spawnDepth": 3},
    )

    result = asyncio.run(boundary.run(deep_request))

    assert live.calls == 0
    assert result.status == "blocked"
    assert result.error_code == "child_spawn_depth_exceeded"


def test_child_runner_boundary_live_path_enforces_total_agents_cap() -> None:
    from magi_agent.runtime.child_runner_boundary import (
        MAX_TOTAL_AGENTS_PER_RUN,
        ChildRunnerConfig,
        LocalChildRunnerBoundary,
    )

    live = LiveChildRunner()
    boundary = LocalChildRunnerBoundary(
        ChildRunnerConfig(enabled=True, liveChildRunnerEnabled=True),
        child_runner=live,
        agents_spawned_so_far=MAX_TOTAL_AGENTS_PER_RUN,
    )

    result = asyncio.run(boundary.run(_request()))

    assert live.calls == 0
    assert result.status == "blocked"
    assert result.error_code == "total_agents_per_run_exceeded"


def test_child_runner_config_live_gate_keeps_production_flags_sealed() -> None:
    from magi_agent.runtime.child_runner_boundary import ChildRunnerConfig

    config = ChildRunnerConfig(enabled=True, liveChildRunnerEnabled=True)

    assert config.live_child_runner_enabled is True
    assert config.production_child_execution_enabled is False
    assert config.production_writes_enabled is False
    # Sealed Literal[False] fields reject any attempt to set them True.
    with pytest.raises(Exception):
        ChildRunnerConfig(productionChildExecutionEnabled=True)
    with pytest.raises(Exception):
        ChildRunnerConfig(productionWritesEnabled=True)


@pytest.mark.parametrize("value", ["1", "true", "TRUE"])
def test_child_runner_config_live_gate_rejects_coercive_strings(value: str) -> None:
    from pydantic import ValidationError

    from magi_agent.runtime.child_runner_boundary import ChildRunnerConfig

    with pytest.raises(ValidationError):
        ChildRunnerConfig(enabled=True, liveChildRunnerEnabled=value)


def test_child_runner_projection_bypasses_cannot_inject_raw_context_or_authority() -> None:
    from magi_agent.runtime.child_runner_boundary import (
        ChildRunnerAuthorityFlags,
        ChildRunnerEnvelopeRef,
        ChildRunnerResult,
    )

    forged_envelope = ChildRunnerEnvelopeRef.model_construct(
        childRef="child:forged",
        taskId="task-1",
        childExecutionId="child-exec-1",
        parentExecutionId="parent-exec-1",
        status="completed",
        summary=(
            "safe summary\n"
            "raw_tool_log: /workspace/bot/private.txt\n"
            "hidden_reasoning: do not expose"
        ),
        evidenceRefs=(
            "evidence:safe-1",
            "evidence:2222222222222222",
            "workspace:child-private",
            "/Users/kevin/private/raw.json",
        ),
        artifactRefs=(
            "artifact:safe-1",
            "artifact:3333333333333333",
            "policy:child-private",
            "s3://private/raw",
        ),
        auditEventRefs=(
            "audit:safe-1",
            "audit:4444444444444444",
            "prompt:child-private",
            "raw child transcript",
        ),
    )
    result = ChildRunnerResult.model_construct(
        status="ok",
        taskId="task-1",
        promptRef="prompt:safe",
        envelope=forged_envelope,
        diagnosticMetadata={
            "rawTranscript": "raw child transcript with sk-child-secret",
            "safeCounter": 1,
        },
        authorityFlags=ChildRunnerAuthorityFlags.model_construct(
            childRunnerAttached=True,
            realChildRunnerExecuted=True,
            parentContextRawInjection=True,
            productionAuthority=True,
        ),
    )
    copied = result.model_copy(
        update={
            "authorityFlags": {
                "childRunnerAttached": True,
                "realChildRunnerExecuted": True,
                "productionAuthority": True,
            }
        }
    )

    encoded = json.dumps(result.public_projection(), sort_keys=True)
    copied_projection = copied.public_projection()

    assert result.envelope is not None
    assert result.envelope.summary == "safe summary"
    assert len(result.envelope.evidence_refs) == 2
    assert len(result.envelope.artifact_refs) == 2
    assert len(result.envelope.audit_event_refs) == 2
    assert _is_runtime_ref(result.envelope.evidence_refs[0], "evidence")
    assert _is_runtime_ref(result.envelope.artifact_refs[0], "artifact")
    assert _is_runtime_ref(result.envelope.audit_event_refs[0], "audit")
    assert "evidence:2222222222222222" not in encoded
    assert "artifact:3333333333333333" not in encoded
    assert "audit:4444444444444444" not in encoded
    assert "workspace:child-private" not in encoded
    assert "policy:child-private" not in encoded
    assert "prompt:child-private" not in encoded
    assert "raw_tool_log" not in encoded
    assert "hidden_reasoning" not in encoded
    assert "/workspace/bot" not in encoded
    assert "/Users/kevin" not in encoded
    assert "s3://private" not in encoded
    assert '"rawTranscript":' not in encoded
    assert "sk-child-secret" not in encoded
    assert copied_projection["authorityFlags"]["childRunnerAttached"] is False
    assert copied_projection["authorityFlags"]["realChildRunnerExecuted"] is False
    assert copied_projection["authorityFlags"]["productionAuthority"] is False


def test_child_runner_public_projection_revalidates_mutated_child_envelope() -> None:
    from magi_agent.runtime.child_runner_boundary import (
        ChildRunnerAuthorityFlags,
        ChildRunnerEnvelopeRef,
        ChildRunnerResult,
    )

    envelope = ChildRunnerEnvelopeRef(
        childRef="child:safe",
        taskId="task-1",
        childExecutionId="child-exec-1",
        parentExecutionId="parent-exec-1",
        status="completed",
        summary="safe summary",
        evidenceRefs=("evidence:safe-1",),
        artifactRefs=("artifact:safe-1",),
        auditEventRefs=("audit:safe-1",),
    )
    result = ChildRunnerResult(
        status="ok",
        taskId="task-1",
        promptRef="prompt:safe",
        envelope=envelope,
        authorityFlags=ChildRunnerAuthorityFlags(),
    )

    object.__setattr__(
        envelope,
        "summary",
        "safe\nraw_tool_log: /workspace/private\nhidden_reasoning: secret",
    )
    object.__setattr__(
        envelope,
        "evidence_refs",
        ("evidence:safe-1", "/Users/kevin/private/raw.json", "raw child transcript"),
    )
    object.__setattr__(envelope, "artifact_refs", ("artifact:safe-1", "s3://private/raw"))
    object.__setattr__(envelope, "audit_event_refs", ("audit:safe-1", "tool_log: raw"))
    object.__setattr__(
        result,
        "authority_flags",
        ChildRunnerAuthorityFlags.model_construct(realChildRunnerExecuted=True),
    )

    projection = result.public_projection()
    encoded = json.dumps(projection, sort_keys=True)

    assert projection["childEnvelope"]["summary"] == "safe"
    assert _is_runtime_ref(projection["parentOutputRefs"][0], "child")
    assert _is_runtime_ref(projection["parentOutputRefs"][1], "evidence")
    assert _is_runtime_ref(projection["parentOutputRefs"][2], "artifact")
    assert _is_runtime_ref(projection["parentOutputRefs"][3], "audit")
    assert "child:safe" not in projection["parentOutputRefs"]
    assert "evidence:safe-1" not in projection["parentOutputRefs"]
    assert "artifact:safe-1" not in projection["parentOutputRefs"]
    assert "audit:safe-1" not in projection["parentOutputRefs"]
    assert "/workspace/private" not in encoded
    assert "/Users/kevin" not in encoded
    assert "s3://private" not in encoded
    assert "raw_tool_log" not in encoded
    assert "hidden_reasoning" not in encoded
    assert "raw child transcript" not in encoded
    assert projection["authorityFlags"]["realChildRunnerExecuted"] is False


def test_child_runner_public_projection_redacts_provider_token_formats() -> None:
    from magi_agent.runtime.child_runner_boundary import (
        ChildRunnerEnvelopeRef,
        ChildRunnerResult,
    )

    result = ChildRunnerResult(
        status="ok",
        taskId="task-1",
        promptRef="prompt:safe",
        envelope=ChildRunnerEnvelopeRef(
            childRef="child:safe",
            taskId="task-1",
            childExecutionId="child-exec-1",
            parentExecutionId="parent-exec-1",
            status="completed",
            summary=(
                "safe summary\n"
                "github_pat_unsafeToken12345\n"
                "xoxb-unsafeToken12345\n"
                "AIzaUnsafeGoogleToken12345"
            ),
        ),
    )
    encoded = json.dumps(result.public_projection(), sort_keys=True)

    assert "safe summary" in encoded
    assert "github_pat_unsafe" not in encoded
    assert "xoxb-unsafe" not in encoded
    assert "AIzaUnsafe" not in encoded


def test_child_runner_public_projection_drops_key_named_credentials() -> None:
    from magi_agent.runtime.child_runner_boundary import ChildRunnerResult

    result = ChildRunnerResult(
        status="disabled",
        taskId="task-1",
        promptRef="prompt:safe",
        diagnosticMetadata={
            "apiKey": "plain-provider-credential",
            "privateKey": "plain-private-key",
            "serviceKey": "plain-service-key",
            "credentialId": "plain-credential-id",
            "authorizationHeader": "plain-auth-header",
            "safeCounter": 1,
        },
    )
    projection = result.public_projection()
    encoded = json.dumps(projection, sort_keys=True)

    assert "plain-provider-credential" not in encoded
    assert "plain-private-key" not in encoded
    assert "plain-service-key" not in encoded
    assert "plain-credential-id" not in encoded
    assert "plain-auth-header" not in encoded
    assert projection["diagnosticMetadata"]["safeCounter"] == 1


def test_child_runner_boundary_imports_no_live_runner_or_runtime_surfaces() -> None:
    module_path = (
        Path(__file__).parents[1]
        / "magi_agent"
        / "runtime"
        / "child_runner_boundary.py"
    )
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    forbidden_prefixes = (
        "google.adk.runners",
        "google.adk.sessions",
        "magi_agent.adk_bridge",
        "magi_agent.tools",
        "magi_agent.transport",
        "magi_agent.memory.adapters",
        "subprocess",
        "socket",
        "httpx",
        "requests",
    )

    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imported_modules
        for prefix in forbidden_prefixes
    )
    for fragment in (
        "__import__(",
        "importlib.import_module",
        "Runner(",
        "SessionService",
        "FunctionTool",
        "subprocess.run",
    ):
        assert fragment not in source


def test_partial_summary_is_redacted_at_model_validation() -> None:
    """The best-effort ``partialSummary`` channel carries RAW child output, so
    its leak-redaction must be intrinsic to the model (field_validator), not
    only the call-site scrub in ``_envelope_from_output``. Validate straight
    from a raw dict with secret/private-path content and assert redaction +
    roundtrip survival.
    """
    from magi_agent.runtime.child_runner_boundary import ChildRunnerEnvelopeRef

    ref = ChildRunnerEnvelopeRef.model_validate(
        {
            "childRef": "child:abc",
            "taskId": "task-1",
            "childExecutionId": "child-exec-1",
            "parentExecutionId": "parent-exec-1",
            "status": "failed",
            "summary": "child_llm_collector_status_failed",
            "partialSummary": (
                "The answer is 2.\n"
                "/Users/kevin/private/notes.txt\n"
                "Authorization: Bearer sk-child-secret\n"
                "chain_of_thought: hidden reasoning"
            ),
        }
    )

    # The usable answer survives...
    assert "The answer is 2." in ref.partial_summary
    # ...but the secrets/paths are stripped by the intrinsic validator.
    assert "/Users/kevin" not in ref.partial_summary
    assert "sk-child-secret" not in ref.partial_summary
    assert "chain_of_thought" not in ref.partial_summary

    # Survives a by-alias dump -> re-validate roundtrip (still redacted).
    roundtrip = ChildRunnerEnvelopeRef.model_validate(ref.model_dump(by_alias=True))
    assert "The answer is 2." in roundtrip.partial_summary
    assert "sk-child-secret" not in roundtrip.partial_summary
