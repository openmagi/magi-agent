from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from typing import Any

from openmagi_core_agent.tools.context import ToolContext
from openmagi_core_agent.tools.manifest import ToolManifest, ToolSource
from openmagi_core_agent.tools.registry import ToolRegistry
from openmagi_core_agent.tools.result import ToolResult


def _context() -> ToolContext:
    return ToolContext(
        botId="bot-pr7",
        userId="user-pr7",
        sessionId="session-pr7-private",
        sessionKey="session://private/session-pr7",
        turnId="turn-pr7",
        workspaceRoot="/Users/kevin/private/workspace",
    )


def _write_manifest() -> ToolManifest:
    return ToolManifest(
        name="WriteFile",
        description="PR7 write test tool",
        kind="native",
        source=ToolSource(kind="native-plugin", package="tests"),
        permission="write",
        inputSchema={
            "type": "object",
            "required": ["path", "content"],
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "additionalProperties": False,
        },
        mutatesWorkspace=True,
        timeoutMs=1000,
        enabled_by_default=True,
    )


def _read_manifest_named_write_file() -> ToolManifest:
    return ToolManifest(
        name="WriteFile",
        description="PR7 read-compatible test tool",
        kind="native",
        source=ToolSource(kind="native-plugin", package="tests"),
        permission="read",
        inputSchema={
            "type": "object",
            "required": ["path", "content"],
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "additionalProperties": False,
        },
        timeoutMs=1000,
        enabled_by_default=True,
    )


def _tool_request(*, content: object = "safe content") -> Any:
    from openmagi_core_agent.tools.kernel import ToolExecutionRequest

    return ToolExecutionRequest(
        toolName="WriteFile",
        toolCallId="/Users/kevin/private/call-id",
        arguments={
            "path": "/Users/kevin/private/secret.txt",
            "content": content,
            "Authorization": "Bearer live-token",
            "command": "cat /Users/kevin/private/secret.txt && echo sk-live-secret",
        },
        context=_context(),
        mode="act",
        exposedToolNames=("WriteFile",),
    )


def _needs_approval_result() -> ToolResult:
    return ToolResult(
        status="needs_approval",
        metadata={
            "toolName": "WriteFile",
            "reason": "workspace mutation requires approval",
            "riskLevel": "high",
            "controlRequest": {
                "requestId": "control-raw-/Users/kevin/private",
                "turnId": "turn-pr7",
                "toolName": "WriteFile",
                "arguments": {
                    "path": "/Users/kevin/private/secret.txt",
                    "Authorization": "Bearer live-token",
                },
                "prompt": "approve raw prompt with sk-live-secret",
            },
        },
    )


def _pending_store() -> tuple[Any, Any]:
    from openmagi_core_agent.runtime.approval_resume import ApprovalResumeStore

    store = ApprovalResumeStore()
    pending = store.create_pending_from_needs_approval(
        _tool_request(),
        _needs_approval_result(),
        now=100,
        expires_at=200,
        transcript_order_refs=("transcript:turn-pr7:0001", "transcript:turn-pr7:0002"),
    )
    return store, pending


def test_pause_request_preserves_safe_identity_digests_and_order_refs() -> None:
    store, pending = _pending_store()

    assert store.durable_writes_enabled is False
    assert store.production_writes_enabled is False
    assert pending.state == "pending"
    assert pending.approval_request.turn_id == "turn-pr7"
    assert pending.approval_request.tool_name == "WriteFile"
    assert pending.approval_request.reason == "workspace mutation requires approval"
    assert pending.approval_request.risk_level == "high"
    assert pending.approval_request.arguments_digest.startswith("sha256:")
    assert pending.approval_request.arguments_ref.startswith("args:")
    assert pending.approval_request.command_preview_digest.startswith("sha256:")
    assert pending.approval_request.command_preview_ref.startswith("command:")
    assert pending.approval_request.target_path_refs
    assert pending.approval_request.transcript_order_refs == (
        "transcript:turn-pr7:0001",
        "transcript:turn-pr7:0002",
    )
    assert store.get_pending(pending.approval_request.request_digest) == pending


def test_request_digest_binds_full_execution_snapshot_and_does_not_overwrite() -> None:
    from openmagi_core_agent.runtime.approval_resume import ApprovalResumeStore

    store = ApprovalResumeStore()
    first_request = _tool_request().model_copy(
        update={"arguments": {"path": "README.md", "content": "safe content"}}
    )
    second_request = first_request.model_copy(
        update={
            "context": _context().model_copy(
                update={
                    "session_id": "session-pr7-other",
                    "session_key": "session://private/session-pr7-other",
                    "workspace_root": "/Users/kevin/other-private/workspace",
                }
            )
        }
    )

    first = store.create_pending_from_needs_approval(
        first_request,
        _needs_approval_result(),
        now=100,
        expires_at=200,
    )
    second = store.create_pending_from_needs_approval(
        second_request,
        _needs_approval_result(),
        now=100,
        expires_at=200,
    )
    duplicate = store.create_pending_from_needs_approval(
        first_request,
        _needs_approval_result(),
        now=101,
        expires_at=201,
    )

    assert first.approval_request.arguments_digest == second.approval_request.arguments_digest
    assert first.approval_request.control_request_id == second.approval_request.control_request_id
    assert first.approval_request.request_digest != second.approval_request.request_digest
    assert store.get_pending(first.approval_request.request_digest) == first
    assert store.get_pending(second.approval_request.request_digest) == second
    assert duplicate == first


def test_request_digest_binds_permission_scope_and_exposed_tool_presence() -> None:
    from openmagi_core_agent.runtime.approval_resume import ApprovalResumeStore

    base_request = _tool_request().model_copy(
        update={"arguments": {"path": "README.md", "content": "safe content"}}
    )
    permission_scoped = base_request.model_copy(
        update={
            "context": _context().model_copy(
                update={"permission_scope": {"mode": "approval-required"}}
            )
        }
    )
    no_exposed_set = base_request.model_copy(update={"exposed_tool_names": None})
    empty_exposed_set = base_request.model_copy(update={"exposed_tool_names": ()})

    permission_store = ApprovalResumeStore()
    base_pending = permission_store.create_pending_from_needs_approval(
        base_request,
        _needs_approval_result(),
        now=100,
        expires_at=200,
    )
    scoped_pending = permission_store.create_pending_from_needs_approval(
        permission_scoped,
        _needs_approval_result(),
        now=100,
        expires_at=200,
    )

    exposure_store = ApprovalResumeStore()
    none_pending = exposure_store.create_pending_from_needs_approval(
        no_exposed_set,
        _needs_approval_result(),
        now=100,
        expires_at=200,
    )
    empty_pending = exposure_store.create_pending_from_needs_approval(
        empty_exposed_set,
        _needs_approval_result(),
        now=100,
        expires_at=200,
    )

    assert base_pending.approval_request.request_digest != (
        scoped_pending.approval_request.request_digest
    )
    assert none_pending.approval_request.request_digest != (
        empty_pending.approval_request.request_digest
    )
    assert permission_store.get_pending(base_pending.approval_request.request_digest) == (
        base_pending
    )
    assert permission_store.get_pending(scoped_pending.approval_request.request_digest) == (
        scoped_pending
    )
    assert exposure_store.get_pending(none_pending.approval_request.request_digest) == (
        none_pending
    )
    assert exposure_store.get_pending(empty_pending.approval_request.request_digest) == (
        empty_pending
    )


def test_approve_resume_token_is_single_use_and_builds_request_with_control_refs() -> None:
    from openmagi_core_agent.runtime.approval_resume import ApprovalResumeStore

    store = ApprovalResumeStore()
    pending = store.create_pending_from_needs_approval(
        _tool_request().model_copy(
            update={"arguments": {"path": "README.md", "content": "safe content"}}
        ),
        _needs_approval_result(),
        now=100,
        expires_at=200,
    )

    approval = store.approve(
        control_request_id=pending.approval_request.control_request_id,
        request_digest=pending.approval_request.request_digest,
        now=120,
    )
    resume = store.resume(
        approval.resume_token,
        control_request_id=pending.approval_request.control_request_id,
        request_digest=pending.approval_request.request_digest,
        now=121,
    )

    assert approval.status == "approved"
    assert approval.resume_token is not None
    assert approval.resume_token not in str(approval.model_dump(by_alias=True))
    assert resume.status == "approved"
    assert resume.execution_allowed is False
    assert resume.handler_called is False
    assert resume.authority_flags.tool_dispatch_allowed is False
    assert approval.resume_token != approval.approval_decision_ref.replace(
        "approval:",
        "resume:",
    )
    derived_public_ref = store.resume(
        approval.approval_decision_ref.replace("approval:", "resume:"),
        control_request_id=pending.approval_request.control_request_id,
        request_digest=pending.approval_request.request_digest,
        now=121,
    )
    assert derived_public_ref.status == "blocked"
    assert derived_public_ref.reason_codes == ("invalid_resume_token",)

    from openmagi_core_agent.runtime.approval_resume import (
        build_tool_execution_request_for_resume,
    )

    resumed_request = build_tool_execution_request_for_resume(resume)

    assert resumed_request.tool_name == "WriteFile"
    assert resumed_request.arguments["content"] == "safe content"
    assert pending.approval_request.control_request_id in resumed_request.control_refs
    assert any(ref.startswith("approval:") for ref in resumed_request.control_refs)

    replay = store.resume(
        approval.resume_token,
        control_request_id=pending.approval_request.control_request_id,
        request_digest=pending.approval_request.request_digest,
        now=122,
    )
    assert replay.status == "blocked"
    assert replay.reason_codes == ("resume_token_reused",)
    assert replay.execution_allowed is False


def test_deny_resume_returns_model_visible_blocked_tool_result_without_private_metadata() -> None:
    store, pending = _pending_store()

    denial = store.deny(
        control_request_id=pending.approval_request.control_request_id,
        request_digest=pending.approval_request.request_digest,
        now=130,
        reason="operator denied raw /Users/kevin/private sk-live-secret",
    )
    result = denial.to_blocked_tool_result()
    dumped = json.dumps(result.model_dump(by_alias=True), sort_keys=True)

    assert denial.status == "denied"
    assert result.status == "blocked"
    assert result.metadata["reasonCode"] == "approval_denied"
    assert result.metadata["requestDigest"] == pending.approval_request.request_digest
    assert result.metadata["controlRequestRef"] == pending.approval_request.control_request_id
    assert "approval:" in result.metadata["approvalDecisionRef"]
    for forbidden in (
        "/Users/kevin",
        "secret.txt",
        "Bearer live-token",
        "sk-live-secret",
        "session://private",
        "approve raw prompt",
    ):
        assert forbidden not in dumped

    blocked_resume = store.resume(
        "resume:invalid",
        control_request_id=pending.approval_request.control_request_id,
        request_digest=pending.approval_request.request_digest,
        now=131,
    )
    assert blocked_resume.status == "blocked"
    assert blocked_resume.execution_allowed is False


def test_invalid_mismatched_and_expired_resume_attempts_fail_closed() -> None:
    store, pending = _pending_store()

    invalid = store.resume(
        "resume:does-not-exist",
        control_request_id=pending.approval_request.control_request_id,
        request_digest=pending.approval_request.request_digest,
        now=110,
    )
    approval = store.approve(
        control_request_id=pending.approval_request.control_request_id,
        request_digest=pending.approval_request.request_digest,
        now=120,
    )
    mismatched = store.resume(
        approval.resume_token,
        control_request_id="control:wrong",
        request_digest=pending.approval_request.request_digest,
        now=121,
    )
    expired = store.resume(
        approval.resume_token,
        control_request_id=pending.approval_request.control_request_id,
        request_digest=pending.approval_request.request_digest,
        now=201,
    )

    assert invalid.status == "blocked"
    assert invalid.reason_codes == ("invalid_resume_token",)
    assert mismatched.status == "blocked"
    assert mismatched.reason_codes == ("approval_request_mismatch",)
    assert expired.status == "blocked"
    assert expired.reason_codes == ("approval_request_expired",)
    assert all(
        decision.execution_allowed is False
        for decision in (invalid, mismatched, expired)
    )


def test_transcript_order_continuity_is_retained_on_resume_decision() -> None:
    store, pending = _pending_store()
    approval = store.approve(
        control_request_id=pending.approval_request.control_request_id,
        request_digest=pending.approval_request.request_digest,
        now=120,
    )
    resume = store.resume(
        approval.resume_token,
        control_request_id=pending.approval_request.control_request_id,
        request_digest=pending.approval_request.request_digest,
        now=121,
    )

    assert resume.transcript_order_refs == (
        "transcript:turn-pr7:0001",
        "transcript:turn-pr7:0002",
    )
    assert resume.pending_tool_call_ref == pending.pending_tool_call_ref


def test_schema_validation_still_blocks_bad_args_after_approval_resume() -> None:
    from openmagi_core_agent.runtime.approval_resume import (
        ApprovalResumeStore,
        build_tool_execution_request_for_resume,
    )
    from openmagi_core_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
    )

    calls: list[dict[str, object]] = []

    class FakeToolExecutor:
        openmagi_local_fake_provider = True

        def execute_tool(
            self,
            *,
            tool_name: str,
            arguments: dict[str, object],
            context: ToolContext,
        ) -> ToolResult:
            calls.append(arguments)
            return ToolResult(status="ok", output={"toolName": tool_name})

    store = ApprovalResumeStore()
    pending = store.create_pending_from_needs_approval(
        _tool_request(content=123),
        _needs_approval_result(),
        now=100,
        expires_at=200,
    )
    approval = store.approve(
        control_request_id=pending.approval_request.control_request_id,
        request_digest=pending.approval_request.request_digest,
        now=120,
    )
    resume = store.resume(
        approval.resume_token,
        control_request_id=pending.approval_request.control_request_id,
        request_digest=pending.approval_request.request_digest,
        now=121,
    )
    resumed_request = build_tool_execution_request_for_resume(resume)

    registry = ToolRegistry()
    registry.register(_write_manifest())
    outcome = asyncio.run(
        ToolExecutionKernel(
            registry,
            config=ToolExecutionKernelConfig(
                enabled=True,
                localFakeHandlerExecutionEnabled=True,
            ),
            local_fake_executor=FakeToolExecutor(),
        ).execute(resumed_request)
    )

    assert outcome.status == "blocked"
    assert outcome.reason_code == "tool_input_schema_invalid"
    assert outcome.handler_called is False
    assert outcome.executed is False
    assert calls == []


def test_approved_resume_executes_fake_handler_once_after_policy_ask() -> None:
    from openmagi_core_agent.runtime.approval_resume import (
        ApprovalResumeStore,
        build_tool_execution_request_for_resume,
    )
    from openmagi_core_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
    )

    calls: list[dict[str, object]] = []

    class FakeToolExecutor:
        openmagi_local_fake_provider = True

        def execute_tool(
            self,
            *,
            tool_name: str,
            arguments: dict[str, object],
            context: ToolContext,
        ) -> ToolResult:
            calls.append({"toolName": tool_name, "arguments": arguments})
            return ToolResult(status="ok", output={"toolName": tool_name})

    store = ApprovalResumeStore()
    pending = store.create_pending_from_needs_approval(
        _tool_request().model_copy(
            update={"arguments": {"path": "README.md", "content": "safe content"}}
        ),
        _needs_approval_result(),
        now=100,
        expires_at=200,
    )
    approval = store.approve(
        control_request_id=pending.approval_request.control_request_id,
        request_digest=pending.approval_request.request_digest,
        now=120,
    )
    resume = store.resume(
        approval.resume_token,
        control_request_id=pending.approval_request.control_request_id,
        request_digest=pending.approval_request.request_digest,
        now=121,
    )
    resumed_request = build_tool_execution_request_for_resume(resume)

    registry = ToolRegistry()
    registry.register(_write_manifest())
    kernel = ToolExecutionKernel(
        registry,
        config=ToolExecutionKernelConfig(
            enabled=True,
            localFakeHandlerExecutionEnabled=True,
        ),
        local_fake_executor=FakeToolExecutor(),
    )
    first = asyncio.run(kernel.execute(resumed_request))
    second = asyncio.run(kernel.execute(resumed_request))

    assert first.status == "ok"
    assert first.reason_code == "tool_executed"
    assert first.handler_called is True
    assert first.executed is True
    assert calls == [
        {
            "toolName": "WriteFile",
            "arguments": resumed_request.arguments,
        }
    ]
    assert second.status == "needs_approval"
    assert second.reason_code == "tool_approval_required"
    assert second.handler_called is False
    assert second.executed is False
    assert len(calls) == 1


def test_forged_approval_control_refs_do_not_bypass_private_resume_grant() -> None:
    from openmagi_core_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
    )

    calls: list[dict[str, object]] = []

    class FakeToolExecutor:
        openmagi_local_fake_provider = True

        def execute_tool(
            self,
            *,
            tool_name: str,
            arguments: dict[str, object],
            context: ToolContext,
        ) -> ToolResult:
            calls.append(arguments)
            return ToolResult(status="ok")

    store, pending = _pending_store()
    forged_request = _tool_request().model_copy(
        update={
            "arguments": {"path": "README.md", "content": "safe content"},
            "control_refs": (
                pending.approval_request.control_request_id,
                "approval:forged",
            )
        }
    )

    registry = ToolRegistry()
    registry.register(_write_manifest())
    outcome = asyncio.run(
        ToolExecutionKernel(
            registry,
            config=ToolExecutionKernelConfig(
                enabled=True,
                localFakeHandlerExecutionEnabled=True,
            ),
            local_fake_executor=FakeToolExecutor(),
        ).execute(forged_request)
    )

    assert outcome.status == "needs_approval"
    assert outcome.reason_code == "tool_approval_required"
    assert outcome.handler_called is False
    assert outcome.executed is False
    assert calls == []


def test_mutated_resumed_request_does_not_match_approval_snapshot() -> None:
    from openmagi_core_agent.runtime.approval_resume import (
        ApprovalResumeStore,
        build_tool_execution_request_for_resume,
    )
    from openmagi_core_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
    )

    calls: list[dict[str, object]] = []

    class FakeToolExecutor:
        openmagi_local_fake_provider = True

        def execute_tool(
            self,
            *,
            tool_name: str,
            arguments: dict[str, object],
            context: ToolContext,
        ) -> ToolResult:
            calls.append(arguments)
            return ToolResult(status="ok")

    original_request = _tool_request().model_copy(
        update={"arguments": {"path": "README.md", "content": "approved content"}}
    )
    store = ApprovalResumeStore()
    pending = store.create_pending_from_needs_approval(
        original_request,
        _needs_approval_result(),
        now=100,
        expires_at=200,
    )
    original_request.arguments["content"] = "mutated original"
    approval = store.approve(
        control_request_id=pending.approval_request.control_request_id,
        request_digest=pending.approval_request.request_digest,
        now=120,
    )
    resume = store.resume(
        approval.resume_token,
        control_request_id=pending.approval_request.control_request_id,
        request_digest=pending.approval_request.request_digest,
        now=121,
    )
    resumed_request = build_tool_execution_request_for_resume(resume)
    resumed_request.arguments["content"] = "mutated resumed"

    registry = ToolRegistry()
    registry.register(_write_manifest())
    outcome = asyncio.run(
        ToolExecutionKernel(
            registry,
            config=ToolExecutionKernelConfig(
                enabled=True,
                localFakeHandlerExecutionEnabled=True,
            ),
            local_fake_executor=FakeToolExecutor(),
        ).execute(resumed_request)
    )

    assert outcome.status == "needs_approval"
    assert outcome.reason_code == "tool_approval_required"
    assert outcome.handler_called is False
    assert outcome.executed is False
    assert calls == []


def test_resume_decision_is_single_use_even_if_current_policy_allows_tool() -> None:
    from openmagi_core_agent.runtime.approval_resume import (
        ApprovalResumeStore,
        build_tool_execution_request_for_resume,
    )
    from openmagi_core_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
    )

    calls: list[dict[str, object]] = []

    class FakeToolExecutor:
        openmagi_local_fake_provider = True

        def execute_tool(
            self,
            *,
            tool_name: str,
            arguments: dict[str, object],
            context: ToolContext,
        ) -> ToolResult:
            calls.append(arguments)
            return ToolResult(status="ok")

    store = ApprovalResumeStore()
    pending = store.create_pending_from_needs_approval(
        _tool_request().model_copy(
            update={"arguments": {"path": "README.md", "content": "safe content"}}
        ),
        _needs_approval_result(),
        now=100,
        expires_at=200,
    )
    approval = store.approve(
        control_request_id=pending.approval_request.control_request_id,
        request_digest=pending.approval_request.request_digest,
        now=120,
    )
    resume = store.resume(
        approval.resume_token,
        control_request_id=pending.approval_request.control_request_id,
        request_digest=pending.approval_request.request_digest,
        now=121,
    )
    resumed_request = build_tool_execution_request_for_resume(resume)

    registry = ToolRegistry()
    registry.register(_read_manifest_named_write_file())
    kernel = ToolExecutionKernel(
        registry,
        config=ToolExecutionKernelConfig(
            enabled=True,
            localFakeHandlerExecutionEnabled=True,
        ),
        local_fake_executor=FakeToolExecutor(),
    )
    first = asyncio.run(kernel.execute(resumed_request))
    second = asyncio.run(kernel.execute(resumed_request))

    assert first.status == "ok"
    assert first.executed is True
    assert second.status == "blocked"
    assert second.reason_code == "approval_resume_invalid_or_reused"
    assert second.executed is False
    assert len(calls) == 1


def test_public_model_dumps_do_not_expose_raw_private_metadata() -> None:
    store, pending = _pending_store()
    approval = store.approve(
        control_request_id=pending.approval_request.control_request_id,
        request_digest=pending.approval_request.request_digest,
        now=120,
    )
    resume = store.resume(
        approval.resume_token,
        control_request_id=pending.approval_request.control_request_id,
        request_digest=pending.approval_request.request_digest,
        now=121,
    )
    public_dump = json.dumps(
        {
            "pending": pending.model_dump(by_alias=True),
            "approval": approval.model_dump(by_alias=True),
            "resume": resume.model_dump(by_alias=True),
            "store": store.public_projection(),
        },
        sort_keys=True,
    )

    for forbidden in (
        "/Users/kevin",
        "secret.txt",
        "Bearer live-token",
        "sk-live-secret",
        "session://private",
        "session-pr7-private",
        "approve raw prompt",
        "Authorization",
        "raw prompt",
        "safe content",
    ):
        assert forbidden not in public_dump
    assert approval.resume_token not in public_dump


def test_auth_payload_variants_are_fully_redacted_from_public_dumps() -> None:
    from openmagi_core_agent.runtime.approval_resume import ApprovalResumeStore
    from openmagi_core_agent.runtime.request_ledger import RequestShapeLedgerResult
    from openmagi_core_agent.tools.kernel import ToolExecutionKernel

    store = ApprovalResumeStore()
    pending = store.create_pending_from_needs_approval(
        _tool_request().model_copy(
            update={
                "arguments": {
                    "path": "README.md",
                    "content": "safe content",
                    "auth": "Authorization: Basic dXNlcjpwYXNz",
                    "bearer": "Bearer abc+/def==",
                    "cookie": "Cookie: sid=abc+/def==",
                }
            }
        ),
        ToolResult(
            status="needs_approval",
            metadata={
                "toolName": "WriteFile",
                "reason": "Authorization: Basic dXNlcjpwYXNz",
                "controlRequest": {"requestId": "control-auth-variant"},
            },
        ),
        now=100,
        expires_at=200,
    )
    sanitized_tool_result = ToolExecutionKernel(
        ToolRegistry()
    )._blocked_outcome(
        _tool_request().model_copy(
            update={"arguments": {"path": "README.md", "content": "safe content"}}
        ),
        ledger_result=RequestShapeLedgerResult(
            status="skipped",
            reason="disabled",
            recorded=False,
        ),
        reason_code="auth_variant_test",
        result=ToolResult(
            status="blocked",
            metadata={
                "reason": "Authorization: Basic dXNlcjpwYXNz Bearer abc+/def==",
                "nested": {"cookie": "Cookie: sid=abc+/def=="},
            },
        ),
        evidence_reason="denied",
        evidence_message="Authorization: Basic dXNlcjpwYXNz",
        tool_id="WriteFile",
    ).result
    public_dump = json.dumps(
        {
            "pending": pending.model_dump(by_alias=True),
            "kernel": sanitized_tool_result.model_dump(by_alias=True),
        },
        sort_keys=True,
        default=str,
    )

    for forbidden in (
        "dXNlcjpwYXNz",
        "abc+/def",
        "sid=abc",
        "Authorization",
        "Basic ",
        "Bearer ",
        "Cookie",
    ):
        assert forbidden not in public_dump


def test_approval_resume_module_import_boundary_stays_live_runner_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("openmagi_core_agent.runtime.approval_resume")
forbidden_prefixes = (
    "google.adk",
    "google.genai",
    "openmagi_core_agent.adk_bridge",
    "openmagi_core_agent.providers",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.routing",
    "openmagi_core_agent.deploy",
    "openmagi_core_agent.chat_proxy",
    "openmagi_core_agent.runtime_selector",
    "openmagi_core_agent.k8s",
    "openmagi_core_agent.tools.kernel",
    "requests",
    "httpx",
    "aiohttp",
    "socket",
    "urllib",
)
loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"forbidden modules loaded: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
