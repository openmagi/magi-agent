from __future__ import annotations

import pytest

from magi_agent.evidence.reports import (
    public_evidence_metadata_report,
    public_evidence_record_report,
    public_evidence_verdict_report,
)
from magi_agent.evidence.rollout import default_audit_before_block_rollout_metadata
from magi_agent.evidence.types import (
    EvidenceContractFailure,
    EvidenceContractScopeMetadata,
    EvidenceContractVerdict,
    EvidenceRecord,
    EvidenceRequirement,
    EvidenceSource,
)


def _record() -> EvidenceRecord:
    return EvidenceRecord(
        type="TestRun",
        status="ok",
        observedAt=40,
        preview="pytest ok Authorization: Bearer secret-token " + ("x" * 500),
        fields={
            "command": "pytest",
            "token": "ghp_livegithubtoken",
            "nested": {"password": "hunter2"},
        },
        source=EvidenceSource(kind="tool_trace", toolName="bash", toolCallId="call-1"),
        metadata={
            "api_key": "sk-live-secret",
            "lastCodeMutation": 35,
            "scope": {
                "agentRoles": ["coding", "research"],
                "runOn": ["main", "child"],
                "spawnDepth": {"minDepth": 0, "maxDepth": 2},
            },
        },
    )


def test_public_evidence_record_report_redacts_and_truncates_without_mutating_record() -> None:
    record = _record()
    report = public_evidence_record_report(record)
    dumped = report.model_dump(by_alias=True)

    assert record.preview is not None
    assert "Bearer secret-token" in record.preview
    assert "Bearer secret-token" not in dumped["preview"]
    assert len(dumped["preview"]) <= 400
    assert dumped["fields"]["command"] == "[redacted]"
    assert dumped["fields"]["token"] == "[redacted]"
    assert dumped["fields"]["nested"] == "[redacted]"
    assert dumped["metadata"]["api_key"] == "[redacted]"
    assert dumped["metadata"]["lastCodeMutation"] == 35
    assert dumped["metadata"]["scope"]["agentRoles"] == ["coding", "research"]
    assert dumped["source"]["toolCallId"] == "call-1"
    assert record.fields["command"] == "pytest"
    assert record.fields["nested"]["password"] == "hunter2"


def test_public_evidence_metadata_report_redacts_and_rejects_non_string_keys() -> None:
    report = public_evidence_metadata_report(
        {
            "authorization": "Bearer metadata-secret",
            "nested": {"api_token": "ghp_nestedsecret", "status": "ok"},
        }
    )

    assert report == {
        "authorization": "[redacted]",
        "nested": {"api_token": "[redacted]", "status": "ok"},
    }

    with pytest.raises(ValueError, match="metadata mapping keys must be strings"):
        public_evidence_metadata_report({1: "numeric-key"})  # type: ignore[dict-item]


def test_public_evidence_record_report_redacts_non_secret_fields_by_default() -> None:
    record = EvidenceRecord(
        type="TestRun",
        status="ok",
        observedAt=45,
        fields={
            "command": "pytest tests",
            "status": "ok",
            "payload": {"result": "passed"},
        },
        source=EvidenceSource(kind="tool_trace", toolName="bash", toolCallId="call-2"),
    )

    report = public_evidence_record_report(record)
    dumped = report.model_dump(by_alias=True)

    assert dumped["fields"] == {
        "command": "[redacted]",
        "status": "[redacted]",
        "payload": "[redacted]",
    }
    assert record.fields["command"] == "pytest tests"
    assert record.fields["payload"]["result"] == "passed"


def test_public_evidence_record_report_exposes_only_public_safe_fields() -> None:
    long_command = "pytest " + ("x" * 500)
    record = EvidenceRecord(
        type="TestRun",
        status="ok",
        observedAt=46,
        fields={
            "command": long_command,
            "status": "ok",
            "api_token": "ghp_livesecret",
            "payload": {
                "status": "passed",
                "password": "hunter2",
                "items": [{"result": "ok", "secret": "nested"}],
            },
            "private_payload": {"result": "hidden"},
        },
        source=EvidenceSource(kind="tool_trace", toolName="bash", toolCallId="call-3"),
        metadata={"publicSafeFields": ("command", "status", "api_token", "payload")},
    )

    report = public_evidence_record_report(record)
    dumped = report.model_dump(by_alias=True)

    assert dumped["fields"]["command"].startswith("pytest ")
    assert len(dumped["fields"]["command"]) <= 400
    assert dumped["fields"]["status"] == "ok"
    assert dumped["fields"]["api_token"] == "[redacted]"
    assert dumped["fields"]["payload"]["status"] == "passed"
    assert dumped["fields"]["payload"]["password"] == "[redacted]"
    assert dumped["fields"]["payload"]["items"][0]["result"] == "ok"
    assert dumped["fields"]["payload"]["items"][0]["secret"] == "[redacted]"
    assert dumped["fields"]["private_payload"] == "[redacted]"
    assert record.fields["command"] == long_command
    assert record.fields["payload"]["password"] == "hunter2"


def test_public_evidence_record_report_redacts_public_safe_credential_fields() -> None:
    record = EvidenceRecord(
        type="TestRun",
        status="ok",
        observedAt=47,
        fields={
            "authorization": "Bearer live-token",
            "cookie": "sessionid=secret-cookie",
            "credentials": "raw-credential",
            "status": "ok",
        },
        source=EvidenceSource(kind="tool_trace", toolName="bash", toolCallId="call-4"),
        metadata={
            "publicSafeFields": (
                "authorization",
                "cookie",
                "credentials",
                "status",
            )
        },
    )

    report = public_evidence_record_report(record)
    dumped = report.model_dump(by_alias=True)

    assert dumped["fields"]["authorization"] == "[redacted]"
    assert dumped["fields"]["cookie"] == "[redacted]"
    assert dumped["fields"]["credentials"] == "[redacted]"
    assert dumped["fields"]["status"] == "ok"
    assert "live-token" not in repr(dumped)
    assert "secret-cookie" not in repr(dumped)
    assert "raw-credential" not in repr(dumped)


def test_public_evidence_record_report_redacts_free_text_public_credentials() -> None:
    raw_preview = (
        "Authorization: Basic dXNlcjpwYXNz "
        "Cookie: sessionid=secret-cookie "
        "ProxyAuthorization=Basic cHJveHk6cGFzcw== "
        "ProxyAuthorization: Basic cHJveHk6cGFzcw== "
        "SetCookie=sessionid=set-cookie-secret "
        "credential=raw-credential"
    )
    record = EvidenceRecord(
        type="TestRun",
        status="ok",
        observedAt=48,
        preview=raw_preview,
        fields={"status": raw_preview},
        source=EvidenceSource(kind="tool_trace", toolName="bash", toolCallId="call-5"),
        metadata={"publicSafeFields": ("status",)},
    )

    report = public_evidence_record_report(record)
    dumped = report.model_dump(by_alias=True)

    for leaked in (
        "dXNlcjpwYXNz",
        "secret-cookie",
        "cHJveHk6cGFzcw==",
        "set-cookie-secret",
        "raw-credential",
    ):
        assert leaked not in repr(dumped)
    assert dumped["preview"] == (
        "Authorization: Basic [redacted] "
        "Cookie: [redacted] "
        "credential=[redacted]"
    )
    assert dumped["fields"]["status"] == (
        "Authorization: Basic [redacted] "
        "Cookie: [redacted] "
        "credential=[redacted]"
    )


def test_public_verdict_report_redacts_matched_evidence_and_failure_metadata() -> None:
    requirement = EvidenceRequirement(type="TestRun", commandPattern="pytest", exitCode=0)
    verdict = EvidenceContractVerdict(
        contractId="coding-tests",
        ok=False,
        state="audit",
        enforcement="audit",
        missingRequirements=(requirement,),
        matchedEvidence=(_record(),),
        failures=(
            EvidenceContractFailure(
                code="EVIDENCE_CONTRACT_FIELD_MISMATCH",
                contractId="coding-tests",
                requirementType="TestRun",
                metadata={"secret": "sk-failure-secret"},
            ),
        ),
    )

    report = public_evidence_verdict_report(verdict)
    dumped = report.model_dump(by_alias=True)

    assert dumped["trafficAttached"] is False
    assert dumped["executionAttached"] is False
    assert dumped["matchedEvidence"][0]["fields"]["token"] == "[redacted]"
    assert dumped["failures"][0]["metadata"]["secret"] == "[redacted]"
    assert dumped["missingRequirements"][0]["type"] == "TestRun"


def test_public_verdict_report_redacts_secret_field_failure_payload_without_mutation() -> None:
    failure = EvidenceContractFailure(
        code="EVIDENCE_CONTRACT_FIELD_MISMATCH",
        contractId="secret-contract",
        requirementType="TestRun",
        metadata={
            "field": "api_token",
            "matcher": "equals",
            "actual": "plain-live-value",
            "expected": "expected-secret",
            "actualValue": {"token": "nested-live-token", "status": "failed"},
            "expectedValue": ["safe-option", {"password": "nested-password"}],
            "value": "direct-secret-value",
            "actualExists": True,
        },
    )
    verdict = EvidenceContractVerdict(
        contractId="secret-contract",
        ok=False,
        state="audit",
        enforcement="audit",
        missingRequirements=(),
        matchedEvidence=(),
        failures=(failure,),
    )

    report = public_evidence_verdict_report(verdict)
    dumped = report.model_dump(by_alias=True)
    metadata = dumped["failures"][0]["metadata"]

    assert metadata["field"] == "api_token"
    assert metadata["matcher"] == "equals"
    assert metadata["actual"] == "[redacted]"
    assert metadata["expected"] == "[redacted]"
    assert metadata["actualValue"]["token"] == "[redacted]"
    assert metadata["actualValue"]["status"] == "[redacted]"
    assert metadata["expectedValue"][0] == "[redacted]"
    assert metadata["expectedValue"][1]["password"] == "[redacted]"
    assert metadata["value"] == "[redacted]"
    assert metadata["actualExists"] is True
    assert failure.metadata["actual"] == "plain-live-value"
    assert failure.metadata["expected"] == "expected-secret"
    assert failure.metadata["actualValue"]["token"] == "nested-live-token"


def test_public_verdict_report_preserves_non_secret_field_failure_payload() -> None:
    failure = EvidenceContractFailure(
        code="EVIDENCE_CONTRACT_FIELD_MISMATCH",
        contractId="status-contract",
        requirementType="TestRun",
        metadata={
            "field": "status",
            "actual": "failed",
            "expected": "passed",
            "value": {"result": "failed"},
        },
    )
    verdict = EvidenceContractVerdict(
        contractId="status-contract",
        ok=False,
        state="audit",
        enforcement="audit",
        missingRequirements=(),
        matchedEvidence=(),
        failures=(failure,),
    )

    report = public_evidence_verdict_report(verdict)
    metadata = report.model_dump(by_alias=True)["failures"][0]["metadata"]

    assert metadata == {
        "field": "status",
        "actual": "failed",
        "expected": "passed",
        "value": {"result": "failed"},
    }


def test_public_verdict_report_redacts_and_truncates_retry_message() -> None:
    retry_message = (
        "Retry with Authorization: Bearer live-token and token=plain-token "
        "using sk-live-secret "
        + ("x" * 500)
    )
    verdict = EvidenceContractVerdict(
        contractId="coding-tests",
        ok=False,
        state="audit",
        enforcement="audit",
        missingRequirements=(),
        matchedEvidence=(),
        failures=(),
        retryMessage=retry_message,
    )

    report = public_evidence_verdict_report(verdict)
    dumped = report.model_dump(by_alias=True)

    assert dumped["retryMessage"] is not None
    assert "Bearer live-token" not in dumped["retryMessage"]
    assert "plain-token" not in dumped["retryMessage"]
    assert "sk-live-secret" not in dumped["retryMessage"]
    assert len(dumped["retryMessage"]) <= 400
    assert verdict.retry_message == retry_message


def test_public_verdict_report_redacts_missing_requirement_secret_matchers_without_mutation() -> None:
    requirement = EvidenceRequirement(
        type="TestRun",
        commandPattern="pytest --token sk-live-secret " + ("x" * 250),
        fields={
            "api_token": {"equals": "ghp_livesecret"},
            "headers": {"oneOf": ("Authorization: Bearer live-token", "safe")},
            "password": {"matches": "password=secret-value"},
            "status": {"equals": "ok"},
        },
    )
    verdict = EvidenceContractVerdict(
        contractId="coding-tests",
        ok=False,
        state="audit",
        enforcement="audit",
        missingRequirements=(requirement,),
        matchedEvidence=(),
        failures=(),
    )

    report = public_evidence_verdict_report(verdict)
    dumped = report.model_dump(by_alias=True)
    missing = dumped["missingRequirements"][0]

    assert requirement.command_pattern is not None
    assert "sk-live-secret" in requirement.command_pattern
    assert missing["commandPattern"] != requirement.command_pattern
    assert "sk-live-secret" not in missing["commandPattern"]
    assert len(missing["commandPattern"]) <= 160
    assert missing["fields"]["api_token"]["equals"] == "[redacted]"
    assert missing["fields"]["headers"]["oneOf"][0] == "[redacted]"
    assert missing["fields"]["password"]["matches"] == "[redacted]"
    assert missing["fields"]["status"]["equals"] == "ok"


def test_public_verdict_report_redacts_nested_secret_keys_in_missing_requirement_matchers() -> None:
    requirement = EvidenceRequirement(
        type="TestRun",
        fields={
            "payload": {"equals": {"password": "hunter2", "status": "ok"}},
            "body": {"oneOf": [{"token": "abc", "result": "passed"}]},
        },
    )
    verdict = EvidenceContractVerdict(
        contractId="coding-tests",
        ok=False,
        state="audit",
        enforcement="audit",
        missingRequirements=(requirement,),
        matchedEvidence=(),
        failures=(),
    )

    report = public_evidence_verdict_report(verdict)
    dumped = report.model_dump(by_alias=True)
    missing_fields = dumped["missingRequirements"][0]["fields"]

    assert missing_fields["payload"]["equals"]["password"] == "[redacted]"
    assert missing_fields["payload"]["equals"]["status"] == "ok"
    assert missing_fields["body"]["oneOf"][0]["token"] == "[redacted]"
    assert missing_fields["body"]["oneOf"][0]["result"] == "passed"
    assert requirement.fields["payload"].equals["password"] == "hunter2"
    assert requirement.fields["body"].one_of[0]["token"] == "abc"


def test_public_verdict_report_redacts_nested_secret_key_non_string_matcher_values() -> None:
    requirement = EvidenceRequirement(
        type="TestRun",
        fields={"payload": {"equals": {"password": 12345, "status": "ok"}}},
    )
    verdict = EvidenceContractVerdict(
        contractId="coding-tests",
        ok=False,
        state="audit",
        enforcement="audit",
        missingRequirements=(requirement,),
        matchedEvidence=(),
        failures=(),
    )

    report = public_evidence_verdict_report(verdict)
    dumped = report.model_dump(by_alias=True)
    missing_fields = dumped["missingRequirements"][0]["fields"]

    assert missing_fields["payload"]["equals"]["password"] == "[redacted]"
    assert missing_fields["payload"]["equals"]["status"] == "ok"
    assert requirement.fields["payload"].equals["password"] == 12345


def test_audit_before_block_rollout_metadata_is_traffic_and_execution_free_with_scope() -> None:
    scope = EvidenceContractScopeMetadata.model_validate(
        {
            "agentRoles": ["coding", "research"],
            "runOn": ["main", "child"],
            "spawnDepth": {"minDepth": 1, "maxDepth": 3},
            "enforcement": "audit",
        }
    )

    rollout = default_audit_before_block_rollout_metadata(scope=scope)
    dumped = rollout.model_dump(by_alias=True)

    assert dumped["trafficAttached"] is False
    assert dumped["executionAttached"] is False
    assert dumped["mode"] == "audit"
    assert dumped["auditBeforeBlock"] is True
    assert dumped["blockModeEnabledForLiveTraffic"] is False
    assert dumped["scope"]["agentRoles"] == ["coding", "research"]
    assert dumped["scope"]["runOn"] == ["main", "child"]
    assert dumped["scope"]["spawnDepth"] == {"minDepth": 1, "maxDepth": 3}


def test_public_reports_do_not_project_evidence_state_into_runner_kwargs() -> None:
    report = public_evidence_record_report(_record())
    dumped = report.model_dump(by_alias=True)

    assert "runnerKwargs" not in dumped
    assert "harnessState" not in dumped
    assert "harness_state" not in dumped


def test_public_evidence_reports_redact_env_style_provider_and_common_token_names() -> None:
    record = EvidenceRecord(
        type="TestRun",
        status="ok",
        observedAt=50,
        preview=(
            "STRIPE_SECRET_KEY=stripe-live-secret "
            "SUPABASE_SERVICE_ROLE_KEY=supabase-service-role "
            "ANTHROPIC_API_KEY=anthropic-live-secret "
            "refresh_token=refresh-token-value "
            "SESSION_TOKEN=session-token-value"
        ),
        fields={
            "status": "ok",
            "STRIPE_SECRET_KEY": "stripe-live-secret",
            "SUPABASE_SERVICE_ROLE_KEY": "supabase-service-role",
            "refresh_token": "refresh-token-value",
        },
        source=EvidenceSource(kind="tool_trace", toolName="bash", toolCallId="call-env"),
        metadata={
            "publicSafeFields": ("status", "STRIPE_SECRET_KEY", "refresh_token"),
            "ANTHROPIC_API_KEY": "anthropic-live-secret",
        },
    )

    report = public_evidence_record_report(record)
    dumped = report.model_dump(by_alias=True)

    public_blob = repr(dumped)
    for leaked in (
        "stripe-live-secret",
        "supabase-service-role",
        "anthropic-live-secret",
        "refresh-token-value",
        "session-token-value",
    ):
        assert leaked not in public_blob
    assert dumped["fields"]["status"] == "ok"
    assert dumped["fields"]["STRIPE_SECRET_KEY"] == "[redacted]"
    assert dumped["fields"]["SUPABASE_SERVICE_ROLE_KEY"] == "[redacted]"
    assert dumped["fields"]["refresh_token"] == "[redacted]"
    assert dumped["metadata"]["ANTHROPIC_API_KEY"] == "[redacted]"


def test_public_evidence_record_report_redacts_source_locators_and_private_paths() -> None:
    record = EvidenceRecord(
        type="SourceInspection",
        status="ok",
        observedAt=49,
        preview=(
            "opened /Users/kevin/private/source.txt from "
            "https://internal.example/customer/acme"
        ),
        fields={
            "status": "read https://internal.example/customer/acme",
            "path": "/Users/kevin/private/source.txt",
        },
        source=EvidenceSource(kind="tool_trace", toolName="WebFetch", toolCallId="call-6"),
        metadata={
            "publicSafeFields": ("status", "path"),
            "safeLabel": "https://internal.example/customer/acme",
        },
    )

    dumped = public_evidence_record_report(record).model_dump(by_alias=True)
    blob = repr(dumped)

    assert dumped["preview"] == "[redacted]"
    assert dumped["fields"]["status"] == "[redacted]"
    assert dumped["fields"]["path"] == "[redacted]"
    assert dumped["metadata"]["safeLabel"] == "[redacted]"
    assert "/Users/kevin" not in blob
    assert "internal.example" not in blob


def test_public_evidence_record_report_hashes_child_identifier_aliases() -> None:
    record = EvidenceRecord(
        type="TestRun",
        status="ok",
        observedAt=51,
        fields={
            "childExecutionId": "child-exec-private-1",
            "childAgentId": "child-agent-private-1",
            "childTaskId": "child-task-private-1",
            "parentAgentId": "parent-agent-private-1",
            "taskId": "task-private-1",
        },
        source=EvidenceSource(kind="execution_contract", contractId="child-boundary"),
        metadata={
            "publicSafeFields": (
                "childExecutionId",
                "childAgentId",
                "childTaskId",
                "parentAgentId",
                "taskId",
            ),
        },
    )

    dumped = public_evidence_record_report(record).model_dump(by_alias=True)
    blob = repr(dumped)

    assert dumped["fields"]["childExecutionId"].startswith("exec:sha256:")
    assert dumped["fields"]["childAgentId"].startswith("agent:sha256:")
    assert dumped["fields"]["childTaskId"].startswith("task:sha256:")
    assert dumped["fields"]["parentAgentId"].startswith("agent:sha256:")
    assert dumped["fields"]["taskId"].startswith("task:sha256:")
    assert "private-1" not in blob
