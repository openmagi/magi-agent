from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from magi_agent.tools.catalog import register_core_tool_manifests
from magi_agent.tools.context import ToolContext
from magi_agent.tools.kernel import (
    ToolExecutionKernel,
    ToolExecutionKernelConfig,
    ToolExecutionRequest,
)
from magi_agent.tools.local_readonly import (
    _PathPolicyError as LocalReadOnlyPathPolicyError,
    _resolve_workspace_path as resolve_local_readonly_workspace_path,
    LocalReadOnlyToolHost,
)
from magi_agent.tools.read_ledger import (
    ReadLedger,
    ReadLedgerConfig,
    WorkspaceMutationReadCheck,
    WorkspaceMutationReadDecision,
    digest_ref,
    is_unsafe_workspace_path,
    safe_workspace_relative_path,
    workspace_content_digest,
    workspace_path_ref,
)
from magi_agent.tools.registry import ToolRegistry


ReferenceToolName = Literal["Read", "Grep", "Glob"]
ReferenceToolStatus = Literal["ok", "blocked", "disabled", "error"]
ManagedReferenceKind = Literal["file", "directory"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_REF_RE = re.compile(r"^managed-ref:[a-f0-9]{24}$")
_PRIVATE_TEXT_RE = re.compile(
    r"(?:"
    r"authorization\s*:|cookie\s*:|bearer\s+|token|secret|session[_-]?key|"
    r"/Users/|/home/|/workspace/|/data/bots/|/private/var/|"
    r"raw[_ -]?(?:tool|child|prompt|transcript|output|result|log|args|text)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought"
    r")",
    re.IGNORECASE,
)
_SEALED_BASENAMES = frozenset(
    {
        "agents.md",
        "claude.md",
        "heartbeat.md",
        "soul.md",
        "tools.md",
    }
)
_PROTECTED_PREFIXES = ("memory/", "docs/superpowers/plans/")


class ReferenceResearchConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_provider_enabled: bool = Field(default=False, alias="localFakeProviderEnabled")
    activation_gate: Literal["local-fixture-reference-cache-only"] = Field(
        default="local-fixture-reference-cache-only",
        alias="activationGate",
    )
    adk_function_tool_surface: Literal["metadata-only"] = Field(
        default="metadata-only",
        alias="adkFunctionToolSurface",
    )
    production_network_enabled: Literal[False] = Field(
        default=False,
        alias="productionNetworkEnabled",
    )
    live_authority_allowed: Literal[False] = Field(
        default=False,
        alias="liveAuthorityAllowed",
    )
    live_tool_execution_enabled: Literal[False] = Field(
        default=False,
        alias="liveToolExecutionEnabled",
    )
    model_call_enabled: Literal[False] = Field(default=False, alias="modelCallEnabled")
    browser_execution_enabled: Literal[False] = Field(
        default=False,
        alias="browserExecutionEnabled",
    )
    channel_delivery_enabled: Literal[False] = Field(
        default=False,
        alias="channelDeliveryEnabled",
    )
    memory_write_enabled: Literal[False] = Field(default=False, alias="memoryWriteEnabled")
    workspace_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="workspaceMutationEnabled",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        for key in (
            "productionNetworkEnabled",
            "liveAuthorityAllowed",
            "liveToolExecutionEnabled",
            "modelCallEnabled",
            "browserExecutionEnabled",
            "channelDeliveryEnabled",
            "memoryWriteEnabled",
            "workspaceMutationEnabled",
        ):
            values[key] = False
        return cls.model_validate(values)


class ReferenceResearchAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    tool_host_dispatched: bool = Field(default=False, alias="toolHostDispatched")
    local_fake_toolhost_dispatched: bool = Field(
        default=False,
        alias="localFakeToolHostDispatched",
    )
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    production_network_enabled: Literal[False] = Field(
        default=False,
        alias="productionNetworkEnabled",
    )
    live_authority_allowed: Literal[False] = Field(
        default=False,
        alias="liveAuthorityAllowed",
    )
    model_call_invoked: Literal[False] = Field(default=False, alias="modelCallInvoked")
    browser_execution_invoked: Literal[False] = Field(
        default=False,
        alias="browserExecutionInvoked",
    )
    channel_delivery_allowed: Literal[False] = Field(
        default=False,
        alias="channelDeliveryAllowed",
    )
    memory_write_allowed: Literal[False] = Field(default=False, alias="memoryWriteAllowed")
    workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="workspaceMutationAllowed",
    )
    raw_source_injected: Literal[False] = Field(default=False, alias="rawSourceInjected")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        for key in (
            "liveToolDispatched",
            "productionNetworkEnabled",
            "liveAuthorityAllowed",
            "modelCallInvoked",
            "browserExecutionInvoked",
            "channelDeliveryAllowed",
            "memoryWriteAllowed",
            "workspaceMutationAllowed",
            "rawSourceInjected",
        ):
            values[key] = False
        return cls.model_validate(values)

    @field_serializer(
        "live_tool_dispatched",
        "production_network_enabled",
        "live_authority_allowed",
        "model_call_invoked",
        "browser_execution_invoked",
        "channel_delivery_allowed",
        "memory_write_allowed",
        "workspace_mutation_allowed",
        "raw_source_injected",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class ReferenceResearchToolResult(BaseModel):
    model_config = _MODEL_CONFIG

    status: ReferenceToolStatus
    output: dict[str, object] | None = None
    llm_output: object | None = Field(default=None, alias="llmOutput")
    transcript_output: object | None = Field(default=None, alias="transcriptOutput")
    error_code: str | None = Field(default=None, alias="errorCode")
    error_message: str | None = Field(default=None, alias="errorMessage")
    metadata: dict[str, object] = Field(default_factory=dict)


class ManagedReference(BaseModel):
    model_config = _MODEL_CONFIG

    ref: str
    kind: ManagedReferenceKind
    path_ref: str = Field(alias="pathRef")
    digest_ref: str = Field(alias="digestRef")
    issued_at: datetime = Field(alias="issuedAt")

    @field_validator("ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        if _REF_RE.fullmatch(value) is None:
            raise ValueError("managed reference must be runtime-issued")
        return value


class _ManagedReferenceRecord(BaseModel):
    model_config = _MODEL_CONFIG

    ref: str
    kind: ManagedReferenceKind
    path: str
    path_ref: str = Field(alias="pathRef")
    digest: str
    session_scope: str = Field(alias="sessionScope")
    turn_scope: str = Field(alias="turnScope")
    workspace_scope: str = Field(alias="workspaceScope")
    workspace_ref: str = Field(alias="workspaceRef")
    read_ledger_entry_ref: str | None = Field(default=None, alias="readLedgerEntryRef")
    issued_at: datetime = Field(alias="issuedAt")

    def public_projection(self) -> dict[str, object]:
        return {
            "ref": self.ref,
            "kind": self.kind,
            "pathRef": self.path_ref,
            "digestRef": digest_ref(self.digest),
            "issuedAt": self.issued_at.isoformat(),
        }


class ManagedReferenceCache:
    """Runtime-issued, local-only reference cache for research read/search adapters."""

    def __init__(self, *, read_ledger: ReadLedger | None = None) -> None:
        self._refs: dict[str, _ManagedReferenceRecord] = {}
        self._read_ledger = read_ledger or ReadLedger(
            ReadLedgerConfig(enabled=True, localInMemoryEnabled=True),
        )

    @property
    def host_call_log(self) -> tuple[str, ...]:
        return ()

    @property
    def read_ledger(self) -> ReadLedger:
        return self._read_ledger

    def issue_path_reference(self, context: ToolContext, *, path: str) -> ManagedReference:
        root = _workspace_root(context)
        relative = _safe_managed_path(path)
        candidate = _resolve_existing_path(root, relative)
        kind: ManagedReferenceKind = "directory" if candidate.is_dir() else "file"
        digest = (
            _directory_digest(root, relative)
            if kind == "directory"
            else _file_digest(candidate)
        )
        workspace_scope = _workspace_scope(context, root)
        workspace_ref = _workspace_ref(context, root)
        path_ref = workspace_path_ref(workspace_ref, relative)
        entry_ref: str | None = None
        if kind == "file":
            stat = candidate.stat()
            entry = self._read_ledger.record_read(
                session_id=_session_scope(context),
                workspace_ref=workspace_ref,
                path=relative,
                digest=digest,
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                read_mode="full",
                turn_id=_turn_scope(context),
                tool_use_id=_tool_use_scope(context),
            )
            entry_ref = entry.entry_ref if entry is not None else None
        record = _ManagedReferenceRecord(
            ref=f"managed-ref:{_short_digest({'scope': workspace_scope, 'path': relative, 'digest': digest})}",
            kind=kind,
            path=relative,
            pathRef=path_ref,
            digest=digest,
            sessionScope=_session_scope(context),
            turnScope=_turn_scope(context),
            workspaceScope=workspace_scope,
            workspaceRef=workspace_ref,
            readLedgerEntryRef=entry_ref,
            issuedAt=datetime.now(UTC),
        )
        self._refs[record.ref] = record
        return ManagedReference(
            ref=record.ref,
            kind=record.kind,
            pathRef=record.path_ref,
            digestRef=digest_ref(record.digest),
            issuedAt=record.issued_at,
        )

    def resolve(self, ref: object) -> _ManagedReferenceRecord | None:
        if not isinstance(ref, str) or _REF_RE.fullmatch(ref) is None:
            return None
        return self._refs.get(ref)


class ReferenceAwareResearchToolBoundary:
    def __init__(
        self,
        *,
        config: ReferenceResearchConfig | Mapping[str, object] | None = None,
        reference_cache: ManagedReferenceCache | None = None,
    ) -> None:
        self.config = ReferenceResearchConfig.model_validate(config or {})
        self.reference_cache = reference_cache or ManagedReferenceCache()
        self._host = LocalReadOnlyToolHost(agent_role="research")

    @property
    def host_call_log(self) -> tuple[str, ...]:
        return self._host.call_log

    async def execute_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> ReferenceResearchToolResult:
        if not self.config.enabled or not self.config.local_fake_provider_enabled:
            return _disabled_result()
        if tool_name not in {"Read", "Grep", "Glob"}:
            return _blocked_result("reference_tool_not_supported")

        record = self.reference_cache.resolve(arguments.get("ref"))
        if record is None:
            return _blocked_result("managed_ref_unissued")
        scope_error = self._scope_error(record, context)
        if scope_error is not None:
            return _blocked_result(scope_error)
        stale_error = self._stale_error(record, context)
        if stale_error is not None:
            return _blocked_result(stale_error)

        if tool_name == "Read":
            return await self._read(record, arguments, context)
        if tool_name == "Grep":
            return await self._grep(record, arguments, context)
        return await self._glob(record, arguments, context)

    async def _read(
        self,
        record: _ManagedReferenceRecord,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> ReferenceResearchToolResult:
        if record.kind != "file":
            return _blocked_result("managed_ref_requires_file")
        read_decision = self.reference_cache.read_ledger.require_fresh_full_read(
            WorkspaceMutationReadCheck(
                sessionId=_session_scope(context),
                workspaceRef=record.workspace_ref,
                path=record.path,
                currentDigest=record.digest,
                mutationKind="edit",
            )
        )
        if read_decision.status != "ok":
            return _blocked_result("managed_ref_read_proof_missing")
        host_args: dict[str, object] = {
            "path": record.path,
            "maxBytes": _bounded_int(arguments.get("maxBytes"), default=8192, maximum=65_536),
        }
        return await self._dispatch(
            requested_tool="Read",
            underlying_tool="FileRead",
            host_args=host_args,
            context=context,
            record=record,
            read_decision=read_decision,
        )

    async def _grep(
        self,
        record: _ManagedReferenceRecord,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> ReferenceResearchToolResult:
        pattern = arguments.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            return _blocked_result("grep_pattern_required")
        glob_value = arguments.get("glob")
        try:
            scoped_glob = _scoped_glob(
                record,
                glob_value if isinstance(glob_value, str) else "**/*",
            )
        except ValueError:
            return _blocked_result("unsafe_managed_reference_pattern")
        host_args: dict[str, object] = {
            "pattern": pattern,
            "glob": scoped_glob,
            "maxMatches": _bounded_int(arguments.get("maxMatches"), default=64, maximum=512),
            "maxFiles": _bounded_int(arguments.get("maxFiles"), default=128, maximum=1024),
            "maxBytes": _bounded_int(arguments.get("maxBytes"), default=8192, maximum=65_536),
        }
        return await self._dispatch(
            requested_tool="Grep",
            underlying_tool="Grep",
            host_args=host_args,
            context=context,
            record=record,
        )

    async def _glob(
        self,
        record: _ManagedReferenceRecord,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> ReferenceResearchToolResult:
        if record.kind != "directory":
            return _blocked_result("managed_ref_requires_directory")
        pattern = arguments.get("pattern")
        try:
            scoped_pattern = _scoped_glob(record, pattern if isinstance(pattern, str) else "*")
        except ValueError:
            return _blocked_result("unsafe_managed_reference_pattern")
        host_args: dict[str, object] = {
            "pattern": scoped_pattern,
            "maxMatches": _bounded_int(arguments.get("maxMatches"), default=64, maximum=1024),
        }
        return await self._dispatch(
            requested_tool="Glob",
            underlying_tool="Glob",
            host_args=host_args,
            context=context,
            record=record,
        )

    async def _dispatch(
        self,
        *,
        requested_tool: ReferenceToolName,
        underlying_tool: Literal["FileRead", "Grep", "Glob"],
        host_args: dict[str, object],
        context: ToolContext,
        record: _ManagedReferenceRecord,
        read_decision: WorkspaceMutationReadDecision | None = None,
    ) -> ReferenceResearchToolResult:
        registry = ToolRegistry()
        register_core_tool_manifests(registry)
        registry.enable(underlying_tool)
        outcome = await ToolExecutionKernel(
            registry,
            config=ToolExecutionKernelConfig(
                enabled=True,
                localFakeHandlerExecutionEnabled=True,
                outputBudgetEnabled=True,
            ),
            local_fake_executor=self._host,
        ).execute(
            ToolExecutionRequest(
                toolName=underlying_tool,
                arguments=host_args,
                context=context,
                mode="act",
                exposedToolNames=(underlying_tool,),
                toolCallId=f"reference:{requested_tool}:{record.ref.removeprefix('managed-ref:')}",
                evidenceRefs=(record.ref,),
            )
        )
        if outcome.result.status != "ok":
            return _blocked_result(_toolhost_error_reason(outcome))
        host_projection = _toolhost_projection(outcome.result.output, outcome.result.metadata)
        authority = ReferenceResearchAuthorityFlags(
            toolHostDispatched=outcome.handler_called,
            localFakeToolHostDispatched=outcome.handler_called,
            liveToolDispatched=False,
            productionNetworkEnabled=False,
            liveAuthorityAllowed=False,
            modelCallInvoked=False,
            browserExecutionInvoked=False,
            channelDeliveryAllowed=False,
            memoryWriteAllowed=False,
            workspaceMutationAllowed=False,
            rawSourceInjected=False,
        )
        output: dict[str, object] = {
            "toolName": requested_tool,
            "underlyingTool": underlying_tool,
            "managedReference": record.public_projection(),
            "toolHost": host_projection,
        }
        if read_decision is not None:
            output["readLedgerDecision"] = read_decision.public_projection()
        metadata: dict[str, object] = {
            "toolName": requested_tool,
            "underlyingTool": underlying_tool,
            "activationGate": self.config.activation_gate,
            "defaultOff": True,
            "localOnly": True,
            "fixtureOnly": True,
            "authorityFlags": authority.model_dump(by_alias=True, mode="python"),
            "toolKernel": {
                "status": outcome.status,
                "reasonCode": outcome.reason_code,
                "handlerCalled": outcome.handler_called,
                "executed": outcome.executed,
            },
            "sourceRefs": host_projection.get("sourceRefs", []),
            "sourceEvidenceReceipts": outcome.result.metadata.get(
                "sourceEvidenceReceipts",
                (),
            ),
            "toolExecutionReceipt": outcome.result.metadata.get("toolExecutionReceipt"),
        }
        if read_decision is not None:
            metadata["readLedgerDecision"] = read_decision.public_projection()
        return ReferenceResearchToolResult(
            status="ok",
            output=output,
            llmOutput={
                "toolName": requested_tool,
                "managedReference": record.public_projection(),
                "sourceRefs": host_projection.get("sourceRefs", []),
            },
            transcriptOutput={
                "toolName": requested_tool,
                "underlyingTool": underlying_tool,
                "sourceRefs": host_projection.get("sourceRefs", []),
            },
            metadata=metadata,
        )

    def _scope_error(
        self,
        record: _ManagedReferenceRecord,
        context: ToolContext,
    ) -> str | None:
        try:
            root = _workspace_root(context)
        except ValueError:
            return "workspace_root_required"
        if (
            record.session_scope != _session_scope(context)
            or record.turn_scope != _turn_scope(context)
            or record.workspace_scope != _workspace_scope(context, root)
        ):
            return "managed_ref_scope_mismatch"
        return None

    def _stale_error(
        self,
        record: _ManagedReferenceRecord,
        context: ToolContext,
    ) -> str | None:
        root = _workspace_root(context)
        try:
            current_path = _resolve_existing_path(root, record.path)
            current_kind: ManagedReferenceKind = "directory" if current_path.is_dir() else "file"
            if current_kind != record.kind:
                return "managed_ref_stale"
            current_digest = (
                _directory_digest(root, record.path)
                if record.kind == "directory"
                else _file_digest(current_path)
            )
        except ValueError:
            return "managed_ref_stale"
        return "managed_ref_stale" if current_digest != record.digest else None


def _disabled_result() -> ReferenceResearchToolResult:
    flags = ReferenceResearchAuthorityFlags().model_dump(by_alias=True, mode="python")
    return ReferenceResearchToolResult(
        status="disabled",
        errorCode="reference_adapter_disabled",
        errorMessage="reference adapter disabled",
        metadata={
            "reason": "reference_adapter_disabled",
            "authorityFlags": flags,
        },
    )


def _blocked_result(reason: str) -> ReferenceResearchToolResult:
    flags = ReferenceResearchAuthorityFlags().model_dump(by_alias=True, mode="python")
    return ReferenceResearchToolResult(
        status="blocked",
        errorCode=reason,
        errorMessage=reason.replace("_", " "),
        metadata={
            "reason": reason,
            "authorityFlags": flags,
        },
    )


def _workspace_root(context: ToolContext) -> Path:
    if not context.workspace_root:
        raise ValueError("workspace root required")
    root = Path(context.workspace_root).resolve()
    if not root.is_dir():
        raise ValueError("workspace root required")
    return root


def _safe_managed_path(path: str) -> str:
    try:
        relative = safe_workspace_relative_path(path)
    except ValueError as error:
        raise ValueError("unsafe managed reference path") from error
    lowered = relative.casefold()
    parts = PurePosixPath(relative).parts
    if (
        any(part.startswith(".") or _PRIVATE_TEXT_RE.search(part) for part in parts)
        or PurePosixPath(relative).name.casefold() in _SEALED_BASENAMES
        or lowered.startswith(_PROTECTED_PREFIXES)
    ):
        raise ValueError("unsafe managed reference path")
    try:
        if is_unsafe_workspace_path(relative):
            raise ValueError("unsafe managed reference path")
    except ValueError as error:
        raise ValueError("unsafe managed reference path") from error
    return relative


def _resolve_existing_path(root: Path, relative: str) -> Path:
    safe_relative = _safe_managed_path(relative)
    try:
        resolved = resolve_local_readonly_workspace_path(
            root,
            safe_relative,
            must_exist=True,
            require_file=False,
        )
    except LocalReadOnlyPathPolicyError as error:
        if error.reason_code == "path_not_found":
            raise ValueError("managed reference path not found") from error
        raise ValueError("unsafe managed reference path")
    if not (resolved.path.is_file() or resolved.path.is_dir()):
        raise ValueError("unsafe managed reference path")
    return resolved.path


def _file_digest(path: Path) -> str:
    raw = path.read_bytes()
    return workspace_content_digest(raw)


def _directory_digest(root: Path, relative: str) -> str:
    base = _resolve_existing_path(root, relative)
    if not base.is_dir():
        raise ValueError("managed reference path is not a directory")
    entries: list[dict[str, object]] = []
    for dirpath, dirnames, filenames in os.walk(base, topdown=True, followlinks=False):
        current_dir = Path(dirpath)
        safe_dirnames: list[str] = []
        for dirname in sorted(dirnames):
            candidate = current_dir / dirname
            if candidate.is_symlink():
                continue
            try:
                rel_dir = candidate.relative_to(root).as_posix()
                _safe_managed_path(rel_dir)
            except ValueError:
                continue
            safe_dirnames.append(dirname)
        dirnames[:] = safe_dirnames
        for filename in sorted(filenames):
            candidate = current_dir / filename
            if candidate.is_symlink():
                continue
            try:
                rel_file = candidate.relative_to(root).as_posix()
                _safe_managed_path(rel_file)
                resolve_local_readonly_workspace_path(
                    root,
                    rel_file,
                    must_exist=True,
                    require_file=True,
                )
            except ValueError:
                continue
            except LocalReadOnlyPathPolicyError:
                continue
            stat = candidate.stat()
            file_digest = _file_digest(candidate)
            entries.append(
                {
                    "pathRef": workspace_path_ref(_workspace_digest_ref(root), rel_file),
                    "digestRef": digest_ref(file_digest),
                    "sizeBytes": stat.st_size,
                    "truncated": False,
                }
            )
    return "sha256:" + hashlib.sha256(
        json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _scoped_glob(record: _ManagedReferenceRecord, pattern: str) -> str:
    clean_pattern = pattern.replace("\\", "/").strip() or "*"
    if clean_pattern.startswith(("/", "~")) or ".." in PurePosixPath(clean_pattern).parts:
        raise ValueError("unsafe managed reference path")
    if _PRIVATE_TEXT_RE.search(clean_pattern):
        raise ValueError("unsafe managed reference path")
    if record.kind == "file":
        return record.path
    root_parts = PurePosixPath(record.path).parts
    pattern_parts = PurePosixPath(clean_pattern).parts
    for part in pattern_parts:
        if (
            part.startswith(".")
            or part.casefold() in _SEALED_BASENAMES
            or _PRIVATE_TEXT_RE.search(part)
        ):
            raise ValueError("unsafe managed reference path")
    joined = str(PurePosixPath(*root_parts, *pattern_parts))
    if not fnmatch.fnmatch(joined, f"{record.path}/**") and joined != record.path:
        return f"{record.path}/{clean_pattern}"
    return joined


def _toolhost_projection(output: object, metadata: Mapping[str, object]) -> dict[str, object]:
    source_refs = _list_text(metadata.get("sourceRefs"))
    projection: dict[str, object] = {
        "status": "ok",
        "sourceRefs": source_refs,
        "sourceEvidenceReceipts": metadata.get("sourceEvidenceReceipts", ()),
        "toolExecutionReceipt": metadata.get("toolExecutionReceipt"),
    }
    if isinstance(output, Mapping):
        if "digest" in output and isinstance(output["digest"], str):
            projection["digestRef"] = digest_ref(output["digest"])
        if "truncated" in output and isinstance(output["truncated"], bool):
            projection["truncated"] = output["truncated"]
        if isinstance(output.get("matches"), list | tuple):
            projection["matchCount"] = len(output["matches"])
            projection["matches"] = _safe_matches(output["matches"])
        if isinstance(output.get("fileCount"), int):
            projection["fileCount"] = output["fileCount"]
    return projection


def _toolhost_error_reason(outcome: object) -> str:
    result = getattr(outcome, "result", None)
    error_code = getattr(result, "error_code", None)
    if isinstance(error_code, str) and error_code.strip():
        return _safe_reason_code(error_code)
    metadata = getattr(result, "metadata", None)
    if isinstance(metadata, Mapping):
        reason = metadata.get("reason")
        if isinstance(reason, str) and reason.strip():
            return _safe_reason_code(reason)
    reason_code = getattr(outcome, "reason_code", None)
    if isinstance(reason_code, str) and reason_code.strip():
        return _safe_reason_code(reason_code)
    return "reference_toolhost_blocked"


def _safe_reason_code(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_:-]+", "_", value.strip().casefold())[:120]
    return normalized or "reference_toolhost_blocked"


def _safe_matches(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list | tuple):
        return []
    matches: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        safe: dict[str, object] = {}
        for key in ("sourceRef", "pathRef", "line", "snippet"):
            nested = item.get(key)
            if isinstance(nested, str):
                safe[key] = _redact_public_text(nested)
            elif isinstance(nested, int):
                safe[key] = nested
        matches.append(safe)
    return matches


def _redact_public_text(value: str) -> str:
    return _PRIVATE_TEXT_RE.sub("[redacted]", value)[:240]


def _list_text(value: object) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return [item for item in value if isinstance(item, str) and not _PRIVATE_TEXT_RE.search(item)]


def _bounded_int(value: object, *, default: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return min(max(value, 1), maximum)
    if isinstance(value, str) and value.isdecimal():
        return min(max(int(value), 1), maximum)
    return default


def _session_scope(context: ToolContext) -> str:
    return f"session:{_short_digest(context.session_id or 'local-session')}"


def _turn_scope(context: ToolContext) -> str:
    return f"turn:{_short_digest(context.turn_id or 'local-turn')}"


def _tool_use_scope(context: ToolContext) -> str:
    return f"tool:{_short_digest(context.tool_use_id or 'local-tool')}"


def _workspace_ref(context: ToolContext, root: Path) -> str:
    value = {
        "runtimeWorkspaceRef": context.workspace_ref or "local-workspace",
        "resolvedRoot": str(root),
    }
    return f"workspace-ref:{_short_digest(value)}"


def _workspace_scope(context: ToolContext, root: Path) -> str:
    return _workspace_ref(context, root)


def _workspace_digest_ref(root: Path) -> str:
    return f"workspace-ref:{_short_digest(str(root))}"


def _short_digest(value: object) -> str:
    raw = (
        value
        if isinstance(value, str)
        else json.dumps(value, sort_keys=True, separators=(",", ":"), default=repr)
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


__all__ = [
    "ManagedReference",
    "ManagedReferenceCache",
    "ReferenceAwareResearchToolBoundary",
    "ReferenceResearchAuthorityFlags",
    "ReferenceResearchConfig",
    "ReferenceResearchToolResult",
]
