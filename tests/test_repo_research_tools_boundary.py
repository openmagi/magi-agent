from __future__ import annotations

import ast
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from runtime_issuance_support import issue_test_runtime_authority
from openmagi_core_agent.evidence.source_ledger import (
    LocalResearchSourceLedger,
    public_source_ledger_report,
)
from openmagi_core_agent.research.source_proof import (
    ResearchSourceOpenReceiptRef,
    ResearchSourceProofRequirement,
    verify_research_source_proof,
)
from openmagi_core_agent.tools.context import ToolContext


class FakeRepoResearchProvider:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def clone(self, request: object) -> dict[str, object]:
        self.calls.append("clone")
        return {
            "repoRef": "repo:fixture:openmagi-core",
            "displayName": "OpenMagi Fixture Repo",
            "summary": (
                "Fixture clone observation only.\n"
                "raw_tool_log: Authorization: " + _fixture_bearer_value() + "\n"
                "/Users/kevin/private/repo"
            ),
            "commitSha": "a" * 40,
            "branch": "main",
            "metadata": {
                "language": "Python",
                "defaultBranch": "main",
                "clonePath": "/Users/kevin/private/repo",
                "rawTree": "README.md\n.env",
                "remoteUrl": _fixture_secret_url(),
                "providerLog": "Authorization: " + _fixture_bearer_value(),
                _fixture_api_key_field(): _fixture_token(),
            },
        }

    async def overview(self, request: object) -> dict[str, object]:
        self.calls.append("overview")
        return {
            "repoRef": "repo:fixture:openmagi-core",
            "displayName": "OpenMagi Fixture Repo",
            "overview": (
                "Digest-safe module overview.\n"
                "raw_tool_log: Cookie: session=unsafe\n"
                "/Users/kevin/private/repo"
            ),
            "commitSha": "b" * 40,
            "branch": "main",
            "metadata": {
                "topLevelPackages": 3,
                "defaultBranch": "main",
                "rawTree": "README.md\n.env",
                "sourceUrl": _fixture_secret_url(),
                "clonePath": "/Users/kevin/private/repo",
            },
        }


class LeakyRepoResearchProvider:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def overview(self, request: object) -> dict[str, object]:
        self.calls.append("overview")
        return {
            "repoRef": "repo:fixture:openmagi-core",
            "displayName": "OpenMagi Fixture Repo",
            "overview": (
                "Digest-safe module overview.\n"
                "/tmp/private-clone/token.txt\n"
                "/var/folders/private-cache/token.txt\n"
                f"session {_fixture_session_value()}\n"
                f"sessionId={_fixture_session_value()}\n"
                f"callbackCode {_fixture_callback_code()}\n"
                f"callbackCode={_fixture_callback_code()}\n"
                f"branch={_fixture_github_pat()}"
            ),
            "commitSha": "c" * 40,
            "branch": _fixture_github_pat(),
            "metadata": {
                "language": "Python",
                "sessionId": _fixture_session_value(),
                "callbackCode": _fixture_callback_code(),
                "note": f"session {_fixture_session_value()}",
                "description": "/var/folders/private-cache/token.txt",
                "label": "/mnt/data/private.txt",
                "tmpPath": "/tmp/private-clone/token.txt",
                "releaseBranch": _fixture_github_pat(),
            },
        }


class FailingLeakyRepoResearchProvider:
    openmagi_local_fake_provider = True

    async def overview(self, request: object) -> dict[str, object]:
        raise RuntimeError(
            "/var/folders/private-cache/token.txt "
            f"session {_fixture_session_value()} "
            f"callbackCode {_fixture_callback_code()}"
        )


def _fixture_token() -> str:
    return "sk" + "-repo-fixture"


def _fixture_bearer_value() -> str:
    return "Bear" + "er repo-fixture-token"


def _fixture_api_key_field() -> str:
    return "api" + "Key"


def _fixture_secret_url() -> str:
    return f"https://github.example.test/private/repo?token={_fixture_token()}"


def _fixture_github_pat() -> str:
    return "github" + "_pat_" + "repoFixtureToken123"


def _fixture_session_value() -> str:
    return "repo-session-token"


def _fixture_callback_code() -> str:
    return "repo-callback-token"


def _context() -> ToolContext:
    return ToolContext(
        botId="bot-1",
        sessionId="session-1",
        turnId="turn-1",
        toolUseId="toolu-repo-1",
    )


def _digest(char: str = "a") -> str:
    return "sha256:" + char * 64


def _repo_source_receipt(
    *,
    source_ref_id: str,
    content_digest: str,
    span_ref: str,
) -> ResearchSourceOpenReceiptRef:
    return ResearchSourceOpenReceiptRef.issue_runtime_source_ref(
        runtime_authority=issue_test_runtime_authority(
            authority_id=f"authority:test-repo-source-ledger-{source_ref_id}",
            scopes=("research_source_proof",),
        ),
        source_ref_id=source_ref_id,
        source_kind="external_repo",
        receipt_kind="opened_snapshot",
        opened=True,
        content_digest=content_digest,
        inspected_at="2026-05-26T12:00:00Z",
        span_refs=(span_ref,),
        redaction_status="metadata_only",
        public_label="Fixture repository",
    )


def test_repo_research_tools_default_off_returns_blocked_without_provider_call() -> None:
    from openmagi_core_agent.web_acquisition.repo_research_tools import (
        LocalRepoResearchRuntime,
        LocalRepoResearchToolBoundary,
        RepoResearchConfig,
    )

    provider = FakeRepoResearchProvider()
    boundary = LocalRepoResearchToolBoundary(
        runtime=LocalRepoResearchRuntime(RepoResearchConfig(), provider=provider)
    )

    for tool_name in ("RepoClone", "RepoOverview"):
        result = asyncio.run(
            boundary.execute_tool(
                tool_name,
                {"repoRef": "repo:fixture:openmagi-core"},
                _context(),
            )
        )

        assert result.status == "blocked"
        assert result.error_code == "repo_research_disabled"
        assert result.output is None
        assert result.llm_output is None
        assert result.transcript_output is None
        assert result.metadata["boundaryStatus"] == "disabled"
        assert result.metadata["attachmentFlags"]["toolHostDispatched"] is False
        assert result.metadata["attachmentFlags"]["workspaceMutated"] is False

    assert provider.calls == []


def test_repo_research_tool_boundary_declares_fixture_only_without_live_authority() -> None:
    from openmagi_core_agent.web_acquisition.repo_research_tools import (
        LocalRepoResearchToolBoundary,
    )

    boundary = LocalRepoResearchToolBoundary()

    assert boundary.fixture_only is True
    assert boundary.tool_host_execution_allowed is False
    assert boundary.live_authority_allowed is False
    assert boundary.workspace_mutation_allowed is False


@pytest.mark.parametrize(
    "arguments",
    (
        {"repoUrl": "https://github.com/openmagi/private"},
        {"repoRef": "https://github.com/openmagi/private"},
        {"repoRef": "git@github.com:openmagi/private.git"},
        {"repoRef": "file:///Users/kevin/private/repo"},
        {"repoRef": "/Users/kevin/private/repo"},
    ),
)
def test_repo_research_tools_block_url_only_private_or_ssh_locators_before_provider_call(
    arguments: dict[str, object],
) -> None:
    from openmagi_core_agent.web_acquisition.repo_research_tools import (
        LocalRepoResearchRuntime,
        LocalRepoResearchToolBoundary,
        RepoResearchConfig,
    )

    provider = FakeRepoResearchProvider()
    boundary = LocalRepoResearchToolBoundary(
        runtime=LocalRepoResearchRuntime(
            RepoResearchConfig(enabled=True, localFakeProviderEnabled=True),
            provider=provider,
        )
    )

    result = asyncio.run(boundary.execute_tool("RepoClone", arguments, _context()))
    encoded = json.dumps(result.model_dump(by_alias=True, mode="python"), sort_keys=True)

    assert result.status == "blocked"
    assert result.error_code in {
        "repo_url_not_allowed_fixture",
        "repo_ref_locator_blocked",
        "repo_ref_private_path_blocked",
    }
    assert provider.calls == []
    assert "github.com/openmagi/private" not in encoded
    assert "git@github.com" not in encoded
    assert "/Users/kevin" not in encoded
    assert "file://" not in encoded


def test_repo_clone_fake_provider_returns_sanitized_digest_only_result() -> None:
    from openmagi_core_agent.web_acquisition.repo_research_tools import (
        LocalRepoResearchRuntime,
        LocalRepoResearchToolBoundary,
        RepoResearchConfig,
    )

    provider = FakeRepoResearchProvider()
    boundary = LocalRepoResearchToolBoundary(
        runtime=LocalRepoResearchRuntime(
            RepoResearchConfig(
                enabled=True,
                localFakeProviderEnabled=True,
                providerId="fake.repo",
            ),
            provider=provider,
        )
    )

    result = asyncio.run(
        boundary.execute_tool(
            "RepoClone",
            {
                "repoRef": "repo:fixture:openmagi-core",
                "commitSha": "a" * 40,
                "branch": "main",
            },
            _context(),
        )
    )
    encoded = json.dumps(result.model_dump(by_alias=True, mode="python"), sort_keys=True)
    output = result.output

    assert provider.calls == ["clone"]
    assert result.status == "ok"
    assert isinstance(output, dict)
    assert output["toolName"] == "RepoClone"
    assert output["operation"] == "repo.clone"
    assert output["providerId"] == "fake.repo"
    assert output["resultRefs"] == ["source:repo:src_1"]
    assert output["sources"][0]["sourceRef"] == "source:repo:src_1"
    assert output["sources"][0]["evidenceRef"] == "evidence:repo:src_1"
    assert output["sources"][0]["normalizedRepoRef"] == "repo:fixture:openmagi-core"
    assert output["sources"][0]["proofType"] == "observed"
    assert output["sources"][0]["contentDigest"].startswith("sha256:")
    assert output["sources"][0]["metadata"] == {
        "language": "Python",
        "defaultBranch": "main",
    }
    assert result.llm_output == output
    assert result.transcript_output == {
        "toolName": "RepoClone",
        "resultRefs": ["source:repo:src_1"],
    }
    assert "clonePath" not in encoded
    assert "rawTree" not in encoded
    assert "remoteUrl" not in encoded
    assert "providerLog" not in encoded
    assert _fixture_api_key_field() not in encoded
    assert _fixture_token() not in encoded
    assert _fixture_bearer_value() not in encoded
    assert "/Users/kevin" not in encoded


def test_repo_research_tools_reject_unmarked_local_fake_provider_before_call() -> None:
    from openmagi_core_agent.web_acquisition.repo_research_tools import (
        LocalRepoResearchRuntime,
        LocalRepoResearchToolBoundary,
        RepoResearchConfig,
    )

    class UnmarkedProvider(FakeRepoResearchProvider):
        openmagi_local_fake_provider = False

    provider = UnmarkedProvider()
    boundary = LocalRepoResearchToolBoundary(
        runtime=LocalRepoResearchRuntime(
            RepoResearchConfig(enabled=True, localFakeProviderEnabled=True),
            provider=provider,
        )
    )

    result = asyncio.run(
        boundary.execute_tool(
            "RepoClone",
            {"repoRef": "repo:fixture:openmagi-core"},
            _context(),
        )
    )

    assert result.status == "blocked"
    assert result.error_code == "local_fake_provider_untrusted"
    assert provider.calls == []


def test_repo_research_provider_exception_does_not_project_private_error_text() -> None:
    from openmagi_core_agent.web_acquisition.repo_research_tools import (
        LocalRepoResearchRuntime,
        LocalRepoResearchToolBoundary,
        RepoResearchConfig,
    )

    boundary = LocalRepoResearchToolBoundary(
        runtime=LocalRepoResearchRuntime(
            RepoResearchConfig(
                enabled=True,
                localFakeProviderEnabled=True,
                providerId="fake.repo",
            ),
            provider=FailingLeakyRepoResearchProvider(),
        )
    )

    result = asyncio.run(
        boundary.execute_tool(
            "RepoOverview",
            {"repoRef": "repo:fixture:openmagi-core"},
            _context(),
        )
    )
    encoded = json.dumps(result.model_dump(by_alias=True, mode="python"), sort_keys=True)

    assert result.status == "blocked"
    assert result.error_code == "local_fake_provider_error"
    assert result.error_message == "local_fake_provider_error"
    assert "/var/folders" not in encoded
    assert "token.txt" not in encoded
    assert "session " not in encoded
    assert "callbackCode" not in encoded
    assert _fixture_session_value() not in encoded
    assert _fixture_callback_code() not in encoded


def test_repo_overview_fake_provider_returns_digest_safe_overview_refs_only() -> None:
    from openmagi_core_agent.web_acquisition.repo_research_tools import (
        LocalRepoResearchRuntime,
        LocalRepoResearchToolBoundary,
        RepoResearchConfig,
    )

    provider = FakeRepoResearchProvider()
    boundary = LocalRepoResearchToolBoundary(
        runtime=LocalRepoResearchRuntime(
            RepoResearchConfig(
                enabled=True,
                localFakeProviderEnabled=True,
                providerId="fake.repo",
            ),
            provider=provider,
        )
    )

    result = asyncio.run(
        boundary.execute_tool(
            "RepoOverview",
            {"repoRef": "repo:fixture:openmagi-core"},
            _context(),
        )
    )
    encoded = json.dumps(result.model_dump(by_alias=True, mode="python"), sort_keys=True)
    output = result.output

    assert provider.calls == ["overview"]
    assert result.status == "ok"
    assert isinstance(output, dict)
    assert output["toolName"] == "RepoOverview"
    assert output["operation"] == "repo.overview"
    assert output["overviewRefs"] == ["source:repo:src_1"]
    assert output["sources"][0]["proofType"] == "opened"
    assert output["sources"][0]["metadata"] == {
        "topLevelPackages": 3,
        "defaultBranch": "main",
    }
    assert output["publicPreview"] == "Digest-safe module overview."
    assert "raw_tool_log" not in encoded
    assert "Cookie:" not in encoded
    assert "sourceUrl" not in encoded
    assert "clonePath" not in encoded
    assert _fixture_token() not in encoded
    assert "/Users/kevin" not in encoded


def test_repo_overview_projection_drops_token_session_callback_and_private_path_output() -> None:
    from openmagi_core_agent.web_acquisition.repo_research_tools import (
        LocalRepoResearchRuntime,
        LocalRepoResearchToolBoundary,
        RepoResearchConfig,
    )

    provider = LeakyRepoResearchProvider()
    boundary = LocalRepoResearchToolBoundary(
        runtime=LocalRepoResearchRuntime(
            RepoResearchConfig(
                enabled=True,
                localFakeProviderEnabled=True,
                providerId="fake.repo",
            ),
            provider=provider,
        )
    )

    result = asyncio.run(
        boundary.execute_tool(
            "RepoOverview",
            {"repoRef": "repo:fixture:openmagi-core"},
            _context(),
        )
    )
    encoded = json.dumps(result.model_dump(by_alias=True, mode="python"), sort_keys=True)
    output = result.output

    assert provider.calls == ["overview"]
    assert result.status == "ok"
    assert isinstance(output, dict)
    assert output["sources"][0]["metadata"] == {"language": "Python"}
    assert "branch" not in output["sources"][0]
    assert output["publicPreview"] == "Digest-safe module overview."
    assert boundary.last_result is not None
    assert boundary.last_result.public_projection()["publicPreview"] == "Digest-safe module overview."
    assert "/tmp/private-clone" not in encoded
    assert "/var/folders" not in encoded
    assert "/mnt/data" not in encoded
    assert "token.txt" not in encoded
    assert "sessionId" not in encoded
    assert "callbackCode" not in encoded
    assert _fixture_session_value() not in encoded
    assert _fixture_callback_code() not in encoded
    assert _fixture_github_pat() not in encoded


def test_repo_record_digest_ignores_private_summary_lines() -> None:
    from openmagi_core_agent.web_acquisition.repo_research_tools import (
        RepoResearchRequest,
        _records_from_provider_output,
    )

    request = RepoResearchRequest(
        operation="repo.overview",
        repoRef="repo:fixture:openmagi-core",
    )
    safe_output = {
        "repoRef": "repo:fixture:openmagi-core",
        "displayName": "OpenMagi Fixture Repo",
        "overview": "Digest-safe module overview.",
        "commitSha": "d" * 40,
        "branch": "main",
        "metadata": {"language": "Python"},
    }
    leaky_output = {
        **safe_output,
        "overview": (
            "Digest-safe module overview.\n"
            "/tmp/private-clone/token.txt\n"
            f"sessionId={_fixture_session_value()}\n"
            f"callbackCode={_fixture_callback_code()}"
        ),
    }

    safe_records = _records_from_provider_output(
        request,
        safe_output,
        provider_id="fake.repo",
        max_results=5,
        max_content_bytes=4_096,
    )
    leaky_records = _records_from_provider_output(
        request,
        leaky_output,
        provider_id="fake.repo",
        max_results=5,
        max_content_bytes=4_096,
    )

    assert leaky_records[0].content_digest == safe_records[0].content_digest


def test_repo_source_record_direct_construction_drops_unsafe_branch_and_metadata() -> None:
    from openmagi_core_agent.web_acquisition.repo_research_tools import (
        RepoResearchSourceRecord,
    )

    record = RepoResearchSourceRecord(
        sourceRef="source:repo:src_1",
        evidenceRef="evidence:repo:src_1",
        method="repo.overview",
        provider="fake.repo",
        normalizedRepoRef="repo:fixture:openmagi-core",
        contentDigest=_digest(),
        proofType="opened",
        branch=_fixture_github_pat(),
        metadata={
            "language": "Python",
            "note": f"session {_fixture_session_value()}",
            "tmpPath": "/tmp/private-clone/token.txt",
        },
    )
    projection = record.public_projection()
    encoded = json.dumps(projection, sort_keys=True)

    assert record.branch is None
    assert projection["branch"] is None
    assert projection["metadata"] == {"language": "Python"}
    assert _fixture_github_pat() not in encoded
    assert _fixture_session_value() not in encoded
    assert "/tmp/private-clone" not in encoded


def test_repo_source_record_metadata_is_immutable_after_validation() -> None:
    from openmagi_core_agent.web_acquisition.repo_research_tools import (
        RepoResearchSourceRecord,
    )

    record = RepoResearchSourceRecord(
        sourceRef="source:repo:src_1",
        evidenceRef="evidence:repo:src_1",
        method="repo.overview",
        provider="fake.repo",
        normalizedRepoRef="repo:fixture:openmagi-core",
        contentDigest=_digest(),
        proofType="opened",
        metadata={"language": "Python"},
    )

    with pytest.raises(TypeError):
        record.metadata["note"] = f"session {_fixture_session_value()}"  # type: ignore[index]

    assert record.public_projection()["metadata"] == {"language": "Python"}


def test_repo_source_record_disables_construct_and_revalidates_copy_updates() -> None:
    from openmagi_core_agent.web_acquisition.repo_research_tools import (
        RepoResearchSourceRecord,
    )

    with pytest.raises(TypeError, match="model_construct is disabled"):
        RepoResearchSourceRecord.model_construct(
            sourceRef="source:repo:src_1",
            evidenceRef="evidence:repo:src_1",
            method="repo.overview",
            provider="fake.repo",
            normalizedRepoRef="repo:fixture:openmagi-core",
            contentDigest=_digest(),
            proofType="opened",
            branch=_fixture_github_pat(),
            metadata={
                "note": f"session {_fixture_session_value()}",
                "tmpPath": "/tmp/private-clone/token.txt",
            },
        )

    record = RepoResearchSourceRecord(
        sourceRef="source:repo:src_1",
        evidenceRef="evidence:repo:src_1",
        method="repo.overview",
        provider="fake.repo",
        normalizedRepoRef="repo:fixture:openmagi-core",
        contentDigest=_digest(),
        proofType="opened",
        branch="main",
        metadata={"language": "Python"},
    )
    copied = record.model_copy(
        update={
            "branch": _fixture_github_pat(),
            "metadata": {
                "language": "Python",
                "note": f"session {_fixture_session_value()}",
                "tmpPath": "/tmp/private-clone/token.txt",
            },
        }
    )

    assert copied.branch is None
    assert copied.metadata == {"language": "Python"}


def test_project_repo_research_result_requires_runtime_opened_receipt_for_ledger_authority() -> None:
    from openmagi_core_agent.web_acquisition.repo_research_tools import (
        LocalRepoResearchRuntime,
        LocalRepoResearchToolBoundary,
        RepoResearchConfig,
        project_repo_research_result_to_source_ledger,
    )

    provider = FakeRepoResearchProvider()
    boundary = LocalRepoResearchToolBoundary(
        runtime=LocalRepoResearchRuntime(
            RepoResearchConfig(
                enabled=True,
                localFakeProviderEnabled=True,
                providerId="fake.repo",
            ),
            provider=provider,
        )
    )
    context = _context()
    tool_result = asyncio.run(
        boundary.execute_tool(
            "RepoOverview",
            {"repoRef": "repo:fixture:openmagi-core"},
            context,
        )
    )
    repo_result = boundary.last_result
    ledger = LocalResearchSourceLedger(
        ledgerId="ledger-1",
        sessionId="session-1",
        turnId="turn-1",
        agentRole="research",
    )

    assert project_repo_research_result_to_source_ledger(
        repo_result,
        ledger,
        context=context,
        tool_name="RepoOverview",
    ) == ()
    assert ledger.snapshot() == ()

    assert repo_result is not None
    source_record = repo_result.records[0]
    receipt = _repo_source_receipt(
        source_ref_id="src_1",
        content_digest=source_record.content_digest,
        span_ref=source_record.evidence_ref,
    )
    records = project_repo_research_result_to_source_ledger(
        repo_result,
        ledger,
        context=context,
        tool_name="RepoOverview",
        source_receipts=(receipt,),
    )
    report = public_source_ledger_report(ledger)
    dumped_report = json.dumps(report.model_dump(by_alias=True), sort_keys=True)

    assert tool_result.status == "ok"
    assert len(records) == 1
    assert records[0].source_id == "src_1"
    assert records[0].turn_id == "turn-1"
    assert records[0].tool_name == "RepoOverview"
    assert records[0].tool_use_id == "toolu-repo-1"
    assert records[0].evidence_type == "SourceInspection"
    assert records[0].kind == "external_repo"
    assert records[0].inspected is True
    assert records[0].content_hash == source_record.content_digest
    assert records[0].metadata["providerId"] == "fake.repo"
    assert records[0].metadata["repoResearchSourceRef"] == "source:repo:src_1"
    assert records[0].metadata["evidenceId"] == "evidence:repo:src_1"
    assert records[0].metadata["method"] == "repo.overview"
    assert records[0].metadata["proofType"] == "runtime_opened_snapshot"
    assert records[0].metadata["normalizedRepoRef"] == "repo:fixture:openmagi-core"
    assert records[0].metadata["sourceReceiptDigest"] == receipt.digest
    assert records[0].metadata["sourceReceiptKind"] == "opened_snapshot"
    assert records[0].metadata["redactionStatus"] == "metadata_only"
    assert records[0].metadata["spanRefs"] == ("evidence:repo:src_1",)
    assert records[0].attachment_flags.live_tool_dispatched is False
    assert records[0].attachment_flags.source_fetched is False
    assert records[0].attachment_flags.production_authority is False
    assert report.attachment_flags.live_tool_dispatched is False
    assert "Authorization" not in dumped_report
    assert _fixture_token() not in dumped_report
    assert "providerLog" not in dumped_report
    assert "clonePath" not in dumped_report
    assert "/Users/kevin" not in dumped_report


def test_project_repo_research_result_rejects_forged_source_receipt_object() -> None:
    from openmagi_core_agent.web_acquisition.repo_research_tools import (
        LocalRepoResearchRuntime,
        LocalRepoResearchToolBoundary,
        RepoResearchConfig,
        project_repo_research_result_to_source_ledger,
    )

    provider = FakeRepoResearchProvider()
    boundary = LocalRepoResearchToolBoundary(
        runtime=LocalRepoResearchRuntime(
            RepoResearchConfig(
                enabled=True,
                localFakeProviderEnabled=True,
                providerId="fake.repo",
            ),
            provider=provider,
        )
    )
    context = _context()
    asyncio.run(
        boundary.execute_tool(
            "RepoOverview",
            {"repoRef": "repo:fixture:openmagi-core"},
            context,
        )
    )
    repo_result = boundary.last_result
    assert repo_result is not None
    source_record = repo_result.records[0]
    runtime_receipt = _repo_source_receipt(
        source_ref_id="src_1",
        content_digest=source_record.content_digest,
        span_ref=source_record.evidence_ref,
    )
    forged_receipt = ResearchSourceOpenReceiptRef.model_validate(
        runtime_receipt.model_dump(by_alias=True, mode="python", warnings=False)
    )
    ledger = LocalResearchSourceLedger(
        ledgerId="ledger-1",
        sessionId="session-1",
        turnId="turn-1",
        agentRole="research",
    )

    with pytest.raises(ValueError, match="issued by the runtime boundary"):
        project_repo_research_result_to_source_ledger(
            repo_result,
            ledger,
            context=context,
            tool_name="RepoOverview",
            source_receipts=(forged_receipt,),
        )
    assert ledger.snapshot() == ()


def test_external_repo_source_proof_requires_runtime_issued_opened_snapshot_not_url_text() -> None:
    receipt = ResearchSourceOpenReceiptRef.issue_runtime_source_ref(
        runtime_authority=issue_test_runtime_authority(
            authority_id="authority:test-repo-source-proof",
            scopes=("research_source_proof",),
        ),
        source_ref_id="src_1",
        source_kind="external_repo",
        receipt_kind="opened_snapshot",
        opened=True,
        content_digest=_digest(),
        inspected_at="2026-05-26T12:00:00Z",
        span_refs=("span:repo-overview",),
        redaction_status="metadata_only",
        public_label="Fixture repository",
    )
    requirement = ResearchSourceProofRequirement(
        sourceRefId="src_1",
        allowedSourceKinds=("external_repo",),
        requiredReceiptKinds=("opened_snapshot",),
        requiredSpanRefs=("span:repo-overview",),
    )

    verdicts = verify_research_source_proof((requirement,), (receipt,))

    assert verdicts[0].verdict == "allowed"
    assert verdicts[0].reason_code == "source_match"

    with pytest.raises(TypeError, match="runtime-issued source ref objects"):
        verify_research_source_proof(
            (requirement,),
            ("https://github.com/openmagi/private",),
        )  # type: ignore[arg-type]


def test_repo_research_tools_import_boundary_has_no_live_git_network_or_toolhost_imports() -> None:
    module_path = (
        Path(__file__).parents[1]
        / "openmagi_core_agent"
        / "web_acquisition"
        / "repo_research_tools.py"
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
        "openmagi_core_agent.adk_bridge",
        "openmagi_core_agent.browser",
        "openmagi_core_agent.tools.catalog",
        "openmagi_core_agent.tools.dispatcher",
        "openmagi_core_agent.tools.kernel",
        "openmagi_core_agent.tools.registry",
        "openmagi_core_agent.transport",
        "openmagi_core_agent.web_acquisition.live_provider_pack",
        "git",
        "dulwich",
        "gitpython",
        "socket",
        "subprocess",
        "httpx",
        "requests",
        "aiohttp",
        "selenium",
        "playwright",
    )
    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imported_modules
        for prefix in forbidden_prefixes
    )
    for fragment in ("__import__(", "importlib.import_module", "requests.get", "httpx."):
        assert fragment not in source

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.web_acquisition.repo_research_tools")
assert hasattr(module, "LocalRepoResearchToolBoundary")

forbidden_loaded = (
    "openmagi_core_agent.adk_bridge.local_toolhost",
    "openmagi_core_agent.tools.dispatcher",
    "openmagi_core_agent.tools.kernel",
    "openmagi_core_agent.web_acquisition.live_provider_pack",
)
loaded = [name for name in forbidden_loaded if name in sys.modules]
if loaded:
    raise AssertionError(f"repo_research_tools import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
