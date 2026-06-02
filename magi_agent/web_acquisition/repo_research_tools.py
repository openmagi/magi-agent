from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import inspect
import json
import re
from types import MappingProxyType
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from magi_agent.evidence.source_ledger import (
    LocalResearchSourceLedger,
    SourceLedgerRecord,
)
from magi_agent.research.source_proof import (
    ResearchSourceOpenReceiptRef,
    ResearchSourceProofRequirement,
    verify_research_source_proof,
)
from magi_agent.tools.result import ToolResult
from magi_agent.web_acquisition.policy import (
    content_digest,
    redact_public_text,
    safe_metadata,
)


RepoResearchOperation = Literal["repo.clone", "repo.overview"]
RepoResearchStatus = Literal["ok", "blocked", "disabled"]
RepoResearchProofType = Literal["observed", "opened"]
RepoResearchToolName = Literal[
    "RepoClone",
    "RepoOverview",
    "FixtureRepoClone",
    "FixtureRepoOverview",
]

_TOOL_OPERATIONS: Mapping[str, RepoResearchOperation] = {
    "RepoClone": "repo.clone",
    "FixtureRepoClone": "repo.clone",
    "RepoOverview": "repo.overview",
    "FixtureRepoOverview": "repo.overview",
}
_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_REPO_REF_RE = re.compile(r"^repo:(?:fixture|managed):[A-Za-z0-9_.:-]{2,120}$")
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_COMMIT_SHA_RE = re.compile(r"^[a-f0-9]{40}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
_SOURCE_ID_RE = re.compile(r"^src_[1-9][0-9]*$")
_PRIVATE_PATH_RE = re.compile(
    r"(?:"
    r"~[\\/][^,\s\"'{}\]\)]+|"
    r"(?<![A-Za-z0-9:/])/(?:[^/,\s\"'{}\]\)]+)(?:/[^,\s\"'{}\]\)]+)+|"
    r"[A-Za-z]:[\\/][^,\s\"'{}\]\)]+|"
    r"\\\\[^,\s\"'{}\]\)]+|"
    r"pvc-[A-Za-z0-9-]+"
    r")",
    re.IGNORECASE,
)
_LOCATOR_TEXT_RE = re.compile(
    r"(?:"
    r"\b(?:https?|file|ssh|git)://[^\s\"'{}\]\)]+|"
    r"\bgit@[A-Za-z0-9_.-]+:[^\s\"'{}\]\)]+"
    r")",
    re.IGNORECASE,
)
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|AKIA[0-9A-Z]{12,}|ASIA[0-9A-Z]{12,}|"
    r"AIza[0-9A-Za-z_-]{12,}|xox[baprs]-[A-Za-z0-9-]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|COOKIE)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_SESSION_OR_CALLBACK_TEXT_RE = re.compile(
    r"\b(?:session(?:id|_id)?|callback(?:code|_code)?|oauth(?:code|_code)?|"
    r"auth(?:code|_code)?|csrf|nonce|state)\s*(?:[:=]|\s+)\s*[^,\s}{\n]{4,}",
    re.IGNORECASE,
)
_UNSAFE_METADATA_KEY_PARTS = frozenset(
    {
        "auth",
        "called",
        "callback",
        "clonepath",
        "code",
        "cookie",
        "credential",
        "debug",
        "enabled",
        "executed",
        "fetched",
        "injected",
        "key",
        "log",
        "network",
        "path",
        "performed",
        "provider",
        "raw",
        "request",
        "response",
        "secret",
        "session",
        "sourceurl",
        "state",
        "token",
        "trace",
        "uri",
        "url",
    }
)


class RepoResearchConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_provider_enabled: bool = Field(default=False, alias="localFakeProviderEnabled")
    provider_id: str = Field(default="openmagi.repo-research.system", alias="providerId")
    max_results: int = Field(default=1, alias="maxResults", ge=1, le=5)
    max_content_bytes: int = Field(default=16_384, alias="maxContentBytes", ge=1)
    adk_function_tool_surface: Literal["future"] = Field(
        default="future",
        alias="adkFunctionToolSurface",
    )
    production_network_enabled: Literal[False] = Field(
        default=False,
        alias="productionNetworkEnabled",
    )
    workspace_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="workspaceMutationEnabled",
    )
    toolhost_dispatch_enabled: Literal[False] = Field(
        default=False,
        alias="toolHostDispatchEnabled",
    )
    live_authority_allowed: Literal[False] = Field(
        default=False,
        alias="liveAuthorityAllowed",
    )


class RepoResearchAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    function_tool_attached: Literal[False] = Field(default=False, alias="functionToolAttached")
    toolhost_dispatched: Literal[False] = Field(default=False, alias="toolHostDispatched")
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    production_network_enabled: Literal[False] = Field(
        default=False,
        alias="productionNetworkEnabled",
    )
    live_git_clone_executed: Literal[False] = Field(default=False, alias="liveGitCloneExecuted")
    workspace_mutated: Literal[False] = Field(default=False, alias="workspaceMutated")
    raw_source_injected: Literal[False] = Field(default=False, alias="rawSourceInjected")
    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )

    @field_serializer(
        "adk_runner_invoked",
        "function_tool_attached",
        "toolhost_dispatched",
        "live_tool_dispatched",
        "production_network_enabled",
        "live_git_clone_executed",
        "workspace_mutated",
        "raw_source_injected",
        "user_visible_output_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class RepoResearchRequest(BaseModel):
    model_config = _MODEL_CONFIG

    operation: RepoResearchOperation
    turn_id: str = Field(default="turn-local", alias="turnId")
    repo_ref: str | None = Field(default=None, alias="repoRef")
    repo_url: str | None = Field(default=None, alias="repoUrl")
    commit_sha: str | None = Field(default=None, alias="commitSha")
    branch: str | None = None
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("turn_id")
    @classmethod
    def _validate_turn_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("turnId must be non-empty")
        return value


class RepoResearchSourceRecord(BaseModel):
    model_config = _MODEL_CONFIG

    source_ref: str = Field(alias="sourceRef")
    evidence_ref: str = Field(alias="evidenceRef")
    method: RepoResearchOperation
    provider: str
    normalized_repo_ref: str = Field(alias="normalizedRepoRef")
    content_digest: str = Field(alias="contentDigest")
    proof_type: RepoResearchProofType = Field(alias="proofType")
    title: str | None = None
    commit_sha: str | None = Field(default=None, alias="commitSha")
    branch: str | None = None
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @classmethod
    def model_construct(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError("model_construct is disabled for repo research source records")

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)

    @field_validator("source_ref", "evidence_ref")
    @classmethod
    def _validate_public_ref(cls, value: str) -> str:
        return _public_ref(value, "ref")

    @field_validator("provider", "title")
    @classmethod
    def _validate_public_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = redact_public_text(value, max_chars=160).strip()
        if not clean:
            return None
        _reject_public_leakage(clean, "repo research text")
        return clean

    @field_validator("normalized_repo_ref")
    @classmethod
    def _validate_normalized_repo_ref(cls, value: str) -> str:
        error = _repo_ref_policy_error(value)
        if error is not None:
            raise ValueError("normalizedRepoRef must be a digest-safe fixture repo ref")
        return value.strip()

    @field_validator("content_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if _DIGEST_RE.fullmatch(value) is None:
            raise ValueError("contentDigest must be a sha256 digest")
        return value

    @field_validator("commit_sha")
    @classmethod
    def _validate_commit_sha(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip().casefold()
        if _COMMIT_SHA_RE.fullmatch(clean) is None:
            return None
        return clean

    @field_validator("branch")
    @classmethod
    def _validate_branch(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_branch(value)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return MappingProxyType(_safe_repo_metadata(value))

    def public_projection(self) -> dict[str, object]:
        return {
            "sourceRef": _public_ref(self.source_ref, "source"),
            "evidenceRef": _public_ref(self.evidence_ref, "evidence"),
            "method": self.method,
            "provider": redact_public_text(self.provider, max_chars=120),
            "normalizedRepoRef": self.normalized_repo_ref,
            "contentDigest": self.content_digest,
            "proofType": self.proof_type,
            "title": redact_public_text(self.title or "", max_chars=160) or None,
            "commitSha": self.commit_sha,
            "branch": self.branch,
            "metadata": _safe_repo_metadata(self.metadata),
        }


class RepoResearchResult(BaseModel):
    model_config = _MODEL_CONFIG

    status: RepoResearchStatus
    operation: RepoResearchOperation
    records: tuple[RepoResearchSourceRecord, ...] = ()
    public_preview: str | None = Field(default=None, alias="publicPreview")
    error_code: str | None = Field(default=None, alias="errorCode")
    error_message: str | None = Field(default=None, alias="errorMessage")
    diagnostic_metadata: Mapping[str, object] = Field(
        default_factory=dict,
        alias="diagnosticMetadata",
    )
    attachment_flags: RepoResearchAttachmentFlags = Field(
        default_factory=RepoResearchAttachmentFlags,
        alias="attachmentFlags",
    )

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)

    def public_projection(self) -> dict[str, object]:
        projected_records = [record.public_projection() for record in self.records]
        return {
            "status": self.status,
            "operation": self.operation,
            "sourceRecords": projected_records,
            "parentOutputRefs": [
                ref
                for record in projected_records
                for ref in (record.get("sourceRef"), record.get("evidenceRef"))
            ],
            "publicPreview": (
                None
                if self.public_preview is None
                else _clean_optional_text(self.public_preview, max_chars=1_024)
            ),
            "errorCode": redact_public_text(self.error_code or "", max_chars=120) or None,
            "diagnosticMetadata": _safe_repo_metadata(self.diagnostic_metadata),
            "attachmentFlags": self.attachment_flags.model_dump(by_alias=True),
        }


class LocalRepoResearchRuntime:
    """Fixture-only repository research boundary.

    The runtime records deterministic metadata from an injected local fake
    provider. It does not clone repositories, mutate workspaces, or dispatch
    through the generic tool host.
    """

    def __init__(self, config: RepoResearchConfig, *, provider: object | None = None) -> None:
        self.config = config
        self.provider = provider

    async def run(self, request: RepoResearchRequest) -> RepoResearchResult:
        diagnostics = _diagnostics(self.config)
        if not self.config.enabled:
            return _result(
                request,
                "disabled",
                error_code="repo_research_disabled",
                diagnostics=diagnostics,
            )

        validation_error = _validate_request(request)
        if validation_error is not None:
            return _result(
                request,
                "blocked",
                error_code=validation_error,
                error_message=validation_error,
                diagnostics=diagnostics,
            )
        if not self.config.local_fake_provider_enabled or self.provider is None:
            return _result(
                request,
                "disabled",
                error_code="local_fake_provider_disabled",
                diagnostics=diagnostics,
            )
        if getattr(self.provider, "openmagi_local_fake_provider", False) is not True:
            return _result(
                request,
                "blocked",
                error_code="local_fake_provider_untrusted",
                diagnostics=diagnostics,
            )

        try:
            provider_output = await self._call_fake_provider(request)
        except Exception as exc:
            error_message = _clean_optional_text(str(exc), max_chars=240)
            return _result(
                request,
                "blocked",
                error_code="local_fake_provider_error",
                error_message=error_message or "local_fake_provider_error",
                diagnostics=diagnostics,
            )
        diagnostics["localFakeProviderCalled"] = True
        records = tuple(
            _records_from_provider_output(
                request,
                provider_output,
                provider_id=self.config.provider_id,
                max_results=self.config.max_results,
                max_content_bytes=self.config.max_content_bytes,
            )
        )
        preview = _public_preview_from_output(
            provider_output,
            max_bytes=self.config.max_content_bytes,
        )
        return RepoResearchResult(
            status="ok",
            operation=request.operation,
            records=records,
            publicPreview=preview,
            diagnosticMetadata=diagnostics,
        )

    async def _call_fake_provider(self, request: RepoResearchRequest) -> object:
        method_name = "clone" if request.operation == "repo.clone" else "overview"
        method = getattr(self.provider, method_name, None)
        if method is None:
            raise ValueError(f"fake provider does not implement {method_name}")
        value = method(request)
        if inspect.isawaitable(value):
            return await value
        return value


class LocalRepoResearchToolBoundary:
    fixture_only: Literal[True] = True
    tool_host_execution_allowed: Literal[False] = False
    live_authority_allowed: Literal[False] = False
    workspace_mutation_allowed: Literal[False] = False

    def __init__(self, *, runtime: LocalRepoResearchRuntime | None = None) -> None:
        self.runtime = runtime or LocalRepoResearchRuntime(RepoResearchConfig())
        self.last_result: RepoResearchResult | None = None

    async def execute_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        context: object | None = None,
    ) -> ToolResult:
        if tool_name not in _TOOL_OPERATIONS:
            return _blocked_tool_result(
                tool_name,
                "repo_research_tool_not_supported",
                boundary_status="blocked",
            )

        request = _request_from_tool(tool_name, arguments, context)
        result = await self.runtime.run(request)
        self.last_result = result
        if result.status != "ok":
            return _tool_result_from_non_ok(tool_name, result)

        output = _tool_output(tool_name, result)
        return ToolResult(
            status="ok",
            output=output,
            llmOutput=output,
            transcriptOutput={
                "toolName": tool_name,
                "resultRefs": [record.source_ref for record in result.records],
            },
            metadata=_safe_tool_metadata(tool_name, result),
        )


def project_repo_research_result_to_source_ledger(
    result: RepoResearchResult | None,
    ledger: LocalResearchSourceLedger,
    *,
    context: object | None = None,
    tool_name: str | None = None,
    source_receipts: Iterable[ResearchSourceOpenReceiptRef] = (),
) -> tuple[SourceLedgerRecord, ...]:
    if result is None or result.status != "ok":
        return ()

    verified_receipts = _runtime_verified_source_receipts(result.records, source_receipts)
    resolved_tool_name = tool_name or _tool_name_for_operation(result.operation)
    turn_id = _context_text(context, "turn_id", "turnId") or ledger.turn_id
    tool_use_id = _context_text(context, "tool_use_id", "toolUseId")
    records: list[SourceLedgerRecord] = []
    for record in result.records:
        source_id = _source_id_from_record(record)
        if source_id is None:
            continue
        source_receipt = verified_receipts.get(source_id)
        if source_receipt is None:
            continue
        payload: dict[str, object] = {
            "turnId": turn_id,
            "toolName": resolved_tool_name,
            "evidenceType": "SourceInspection",
            "kind": "external_repo",
            "uri": record.normalized_repo_ref,
            "inspected": True,
            "contentHash": source_receipt.content_digest,
            "metadata": {
                "providerId": redact_public_text(record.provider, max_chars=120),
                "repoResearchSourceRef": record.source_ref,
                "evidenceId": record.evidence_ref,
                "method": record.method,
                "proofType": "runtime_opened_snapshot",
                "normalizedRepoRef": record.normalized_repo_ref,
                "sourceReceiptDigest": source_receipt.digest,
                "sourceReceiptKind": source_receipt.receipt_kind,
                "redactionStatus": source_receipt.redaction_status,
                "spanRefs": source_receipt.span_refs,
            },
        }
        if tool_use_id is not None:
            payload["toolUseId"] = tool_use_id
        if record.title is not None:
            payload["title"] = record.title
        records.append(ledger.record_source(payload))
    return tuple(records)


def _request_from_tool(
    tool_name: str,
    arguments: Mapping[str, object],
    context: object | None,
) -> RepoResearchRequest:
    base: dict[str, object] = {
        "operation": _TOOL_OPERATIONS[tool_name],
        "turnId": _context_text(context, "turn_id", "turnId") or "turn-local",
    }
    repo_ref = _string_arg(arguments, "repoRef", "repo_ref", "ref")
    repo_url = _string_arg(arguments, "repoUrl", "repo_url", "url")
    commit_sha = _string_arg(arguments, "commitSha", "commit", "sha")
    branch = _string_arg(arguments, "branch")
    metadata = arguments.get("metadata")
    if repo_ref is not None:
        base["repoRef"] = repo_ref
    if repo_url is not None:
        base["repoUrl"] = repo_url
    if commit_sha is not None:
        base["commitSha"] = commit_sha
    if branch is not None:
        base["branch"] = branch
    if isinstance(metadata, Mapping):
        base["metadata"] = dict(metadata)
    return RepoResearchRequest.model_validate(base)


def _tool_result_from_non_ok(tool_name: str, result: RepoResearchResult) -> ToolResult:
    return ToolResult(
        status="blocked",
        errorCode=result.error_code,
        errorMessage=_clean_optional_text(result.error_message or "", max_chars=240),
        metadata=_safe_tool_metadata(tool_name, result),
    )


def _blocked_tool_result(
    tool_name: str,
    error_code: str,
    *,
    boundary_status: str,
) -> ToolResult:
    return ToolResult(
        status="blocked",
        errorCode=error_code,
        metadata={
            "toolName": tool_name,
            "boundaryStatus": boundary_status,
            "attachmentFlags": _default_attachment_flags(),
        },
    )


def _tool_output(tool_name: str, result: RepoResearchResult) -> dict[str, object]:
    provider_id = _provider_id(result.records)
    sources = [_source_output(record) for record in result.records]
    output: dict[str, object] = {
        "toolName": tool_name,
        "operation": result.operation,
        "providerId": provider_id,
        "sources": sources,
    }
    if result.operation == "repo.clone":
        output["resultRefs"] = [record.source_ref for record in result.records]
    else:
        output["overviewRefs"] = [record.source_ref for record in result.records]
    preview = _clean_optional_text(result.public_preview, max_chars=1_024)
    if preview is not None:
        output["publicPreview"] = preview
    return output


def _source_output(record: RepoResearchSourceRecord) -> dict[str, object]:
    output: dict[str, object] = {
        "sourceRef": record.source_ref,
        "evidenceRef": record.evidence_ref,
        "normalizedRepoRef": record.normalized_repo_ref,
        "contentDigest": record.content_digest,
        "proofType": record.proof_type,
        "metadata": _safe_repo_metadata(record.metadata),
    }
    if record.title is not None:
        output["title"] = record.title
    if record.commit_sha is not None:
        output["commitSha"] = record.commit_sha
    if record.branch is not None:
        output["branch"] = record.branch
    return output


def _safe_tool_metadata(tool_name: str, result: RepoResearchResult) -> dict[str, object]:
    projection = result.public_projection()
    return {
        "toolName": tool_name,
        "boundaryStatus": result.status,
        "errorCode": result.error_code,
        "parentOutputRefs": projection["parentOutputRefs"],
        "attachmentFlags": projection["attachmentFlags"],
    }


def _diagnostics(config: RepoResearchConfig) -> dict[str, object]:
    return {
        "enabled": config.enabled,
        "localFakeProviderEnabled": config.local_fake_provider_enabled,
        "productionNetworkEnabled": False,
        "workspaceMutationEnabled": False,
        "toolHostDispatchEnabled": False,
        "liveAuthorityAllowed": False,
        "adkFunctionToolSurface": config.adk_function_tool_surface,
        "localFakeProviderCalled": False,
    }


def _result(
    request: RepoResearchRequest,
    status: RepoResearchStatus,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
    diagnostics: Mapping[str, object],
) -> RepoResearchResult:
    return RepoResearchResult(
        status=status,
        operation=request.operation,
        errorCode=error_code,
        errorMessage=error_message,
        diagnosticMetadata=diagnostics,
    )


def _validate_request(request: RepoResearchRequest) -> str | None:
    if request.repo_url:
        return "repo_url_not_allowed_fixture"
    if request.repo_ref is None or not request.repo_ref.strip():
        return "repo_ref_required"
    repo_ref_error = _repo_ref_policy_error(request.repo_ref)
    if repo_ref_error is not None:
        return repo_ref_error
    if request.commit_sha is not None and _safe_commit_sha(request.commit_sha) is None:
        return "commit_sha_invalid"
    if request.branch is not None and _safe_branch(request.branch) is None:
        return "branch_invalid"
    return None


def _records_from_provider_output(
    request: RepoResearchRequest,
    provider_output: object,
    *,
    provider_id: str,
    max_results: int,
    max_content_bytes: int,
) -> list[RepoResearchSourceRecord]:
    raw_records = _raw_source_items(provider_output)
    records: list[RepoResearchSourceRecord] = []
    for index, item in enumerate(raw_records[:max_results], start=1):
        normalized_repo_ref = _normalized_repo_ref(request, item)
        title = _optional_text(item.get("displayName") or item.get("title"))
        metadata = _safe_repo_metadata(item.get("metadata"))
        summary = _source_summary(item, provider_output, max_bytes=max_content_bytes)
        records.append(
            RepoResearchSourceRecord(
                sourceRef=_source_ref(index),
                evidenceRef=_evidence_ref(index),
                method=request.operation,
                provider=provider_id,
                normalizedRepoRef=normalized_repo_ref,
                contentDigest=_record_content_digest(
                    operation=request.operation,
                    normalized_repo_ref=normalized_repo_ref,
                    title=title,
                    summary=summary,
                    commit_sha=_safe_commit_sha(item.get("commitSha"))
                    or _safe_commit_sha(request.commit_sha),
                    branch=_safe_branch(item.get("branch")) or _safe_branch(request.branch),
                    metadata=metadata,
                ),
                proofType="opened" if request.operation == "repo.overview" else "observed",
                title=title,
                commitSha=_safe_commit_sha(item.get("commitSha"))
                or _safe_commit_sha(request.commit_sha),
                branch=_safe_branch(item.get("branch")) or _safe_branch(request.branch),
                metadata=metadata,
            )
        )
    return records


def _raw_source_items(provider_output: object) -> list[Mapping[str, object]]:
    if isinstance(provider_output, Mapping):
        raw_results = provider_output.get("results") or provider_output.get("sources")
        if isinstance(raw_results, list | tuple):
            return [item for item in raw_results if isinstance(item, Mapping)]
        return [provider_output]
    if isinstance(provider_output, list | tuple):
        return [item for item in provider_output if isinstance(item, Mapping)]
    return [{}]


def _normalized_repo_ref(
    request: RepoResearchRequest,
    item: Mapping[str, object],
) -> str:
    item_ref = item.get("repoRef") or item.get("normalizedRepoRef")
    if isinstance(item_ref, str) and _repo_ref_policy_error(item_ref) is None:
        return item_ref.strip()
    if request.repo_ref is not None:
        return request.repo_ref.strip()
    return "repo:fixture:redacted"


def _record_content_digest(
    *,
    operation: str,
    normalized_repo_ref: str,
    title: str | None,
    summary: str | None,
    commit_sha: str | None,
    branch: str | None,
    metadata: Mapping[str, object],
) -> str:
    material = {
        "operation": operation,
        "normalizedRepoRef": normalized_repo_ref,
        "title": title,
        "summary": summary,
        "commitSha": commit_sha,
        "branch": branch,
        "metadata": dict(sorted(metadata.items(), key=lambda item: str(item[0]))),
    }
    return content_digest(json.dumps(material, sort_keys=True, separators=(",", ":")))


def _source_summary(
    item: Mapping[str, object],
    provider_output: object,
    *,
    max_bytes: int,
) -> str | None:
    for key in ("overview", "summary", "preview", "content", "text"):
        value = item.get(key)
        if isinstance(value, str):
            return _clean_optional_text(value, max_chars=min(max_bytes, 1_024))
    if isinstance(provider_output, Mapping):
        for key in ("overview", "summary", "preview", "content", "text"):
            value = provider_output.get(key)
            if isinstance(value, str):
                return _clean_optional_text(value, max_chars=min(max_bytes, 1_024))
    return None


def _public_preview_from_output(provider_output: object, *, max_bytes: int) -> str | None:
    if isinstance(provider_output, Mapping):
        for key in ("overview", "summary", "preview", "content", "text"):
            value = provider_output.get(key)
            if isinstance(value, str):
                return _clean_optional_text(value, max_chars=min(max_bytes, 1_024))
    return None


def _safe_repo_metadata(metadata: object) -> dict[str, object]:
    safe = safe_metadata(dict(metadata) if isinstance(metadata, Mapping) else metadata)
    projected: dict[str, object] = {}
    for key, value in safe.items():
        normalized_key = _normalized_key(key)
        if any(part in normalized_key for part in _UNSAFE_METADATA_KEY_PARTS):
            continue
        if isinstance(value, str):
            clean = redact_public_text(value, max_chars=256).strip()
            if not clean or _contains_private_or_locator_text(clean):
                continue
            projected[str(key)] = clean
        elif isinstance(value, bool | int | float) or value is None:
            projected[str(key)] = value
    return projected


def _repo_ref_policy_error(repo_ref: str) -> str | None:
    clean = repo_ref.strip()
    if not clean:
        return "repo_ref_required"
    if _PRIVATE_PATH_RE.search(clean):
        return "repo_ref_private_path_blocked"
    if _LOCATOR_TEXT_RE.search(clean):
        return "repo_url_not_allowed_fixture"
    if clean.startswith("/") or clean.startswith("."):
        return "repo_ref_private_path_blocked"
    if _SECRET_TEXT_RE.search(clean):
        return "repo_ref_locator_blocked"
    if _REPO_REF_RE.fullmatch(clean) is None:
        return "repo_ref_locator_blocked"
    return None


def _safe_commit_sha(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    clean = value.strip().casefold()
    if _COMMIT_SHA_RE.fullmatch(clean) is None:
        return None
    return clean


def _safe_branch(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    clean = value.strip()
    if _BRANCH_RE.fullmatch(clean) is None:
        return None
    if _contains_private_or_locator_text(clean):
        return None
    return clean


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = redact_public_text(value, max_chars=160).strip()
    if not text or _contains_private_or_locator_text(text):
        return None
    return text


def _clean_optional_text(value: object, *, max_chars: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = redact_public_text(value, max_chars=max_chars).strip()
    if not text:
        return None
    safe_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not _contains_private_or_locator_text(line)
    ]
    return "\n".join(safe_lines) or None


def _runtime_verified_source_receipts(
    records: tuple[RepoResearchSourceRecord, ...],
    source_receipts: Iterable[ResearchSourceOpenReceiptRef],
) -> dict[str, ResearchSourceOpenReceiptRef]:
    receipts = tuple(source_receipts)
    eligible_records: dict[str, RepoResearchSourceRecord] = {}
    for record in records:
        source_id = _source_id_from_record(record)
        if source_id is not None and record.proof_type == "opened":
            eligible_records[source_id] = record
    if not eligible_records or not receipts:
        return {}

    requirements = tuple(
        ResearchSourceProofRequirement(
            sourceRefId=source_id,
            allowedSourceKinds=("external_repo",),
            requiredReceiptKinds=("opened_snapshot",),
            requiredSpanRefs=(record.evidence_ref,),
        )
        for source_id, record in eligible_records.items()
    )
    verdicts = verify_research_source_proof(requirements, receipts)
    receipt_by_source_id = {
        receipt.source_ref_id: receipt
        for receipt in receipts
        if receipt.source_ref_id in eligible_records
    }
    verified: dict[str, ResearchSourceOpenReceiptRef] = {}
    for verdict in verdicts:
        if verdict.verdict != "allowed" or verdict.content_digest is None:
            continue
        record = eligible_records.get(verdict.source_ref_id)
        receipt = receipt_by_source_id.get(verdict.source_ref_id)
        if record is None or receipt is None:
            continue
        if verdict.content_digest != record.content_digest:
            continue
        if receipt.content_digest != record.content_digest:
            continue
        verified[verdict.source_ref_id] = receipt
    return verified


def _string_arg(arguments: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = arguments.get(key)
        if isinstance(value, str):
            return value
    return None


def _context_text(context: object | None, *names: str) -> str | None:
    if context is None:
        return None
    for name in names:
        value = getattr(context, name, None)
        if isinstance(value, str) and value.strip():
            return value
    if isinstance(context, Mapping):
        for name in names:
            value = context.get(name)
            if isinstance(value, str) and value.strip():
                return value
    return None


def _tool_name_for_operation(operation: str) -> str:
    if operation == "repo.clone":
        return "RepoClone"
    return "RepoOverview"


def _provider_id(records: tuple[RepoResearchSourceRecord, ...]) -> str:
    if not records:
        return "openmagi.repo-research.system"
    return redact_public_text(records[0].provider, max_chars=120)


def _source_id_from_record(record: RepoResearchSourceRecord) -> str | None:
    candidate = record.source_ref.rsplit(":", maxsplit=1)[-1]
    if _SOURCE_ID_RE.fullmatch(candidate) is None:
        return None
    return candidate


def _source_ref(source_index: int) -> str:
    return f"source:repo:src_{source_index}"


def _evidence_ref(source_index: int) -> str:
    return f"evidence:repo:src_{source_index}"


def _public_ref(value: str, prefix: str) -> str:
    text = str(value)
    clean = redact_public_text(text, max_chars=180).strip()
    if clean == text.strip() and _PUBLIC_REF_RE.fullmatch(clean):
        return clean
    return f"{prefix}:{hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]}"


def _default_attachment_flags() -> dict[str, bool]:
    return RepoResearchAttachmentFlags().model_dump(by_alias=True)


def _reject_public_leakage(value: str, field_name: str) -> None:
    if _contains_private_or_locator_text(value):
        raise ValueError(f"{field_name} must be digest-safe metadata")


def _contains_private_or_locator_text(value: str) -> bool:
    return bool(
        _PRIVATE_PATH_RE.search(value)
        or _LOCATOR_TEXT_RE.search(value)
        or _SECRET_TEXT_RE.search(value)
        or _SESSION_OR_CALLBACK_TEXT_RE.search(value)
        or "[redacted" in value
    )


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


__all__ = [
    "LocalRepoResearchRuntime",
    "LocalRepoResearchToolBoundary",
    "RepoResearchAttachmentFlags",
    "RepoResearchConfig",
    "RepoResearchOperation",
    "RepoResearchRequest",
    "RepoResearchResult",
    "RepoResearchSourceRecord",
    "RepoResearchStatus",
    "RepoResearchToolName",
    "project_repo_research_result_to_source_ledger",
]
