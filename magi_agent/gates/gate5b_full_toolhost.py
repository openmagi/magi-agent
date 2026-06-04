from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import logging
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Self

from magi_agent.config.env import MAGI_EDIT_FUZZY_MATCH_ENABLED as _EDIT_FUZZY_MATCH_ENABLED

from google.adk.tools import FunctionTool
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from magi_agent.coding.lsp_client import (
    Diagnostic,
    DiagnosticsProvider,
    LspClient,
    collect_error_diagnostics,
    format_diagnostics_block,
    language_id_for_path,
)
from magi_agent.evidence.code_diagnostics_receipts import (
    CodeDiagnosticsBoundary,
    CodeDiagnosticsRecord,
)
from magi_agent.evidence.coding_tool_receipts import (
    CodingToolReceiptBoundary,
    CodingToolReceiptConfig,
    CodingToolReceiptRecord,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.dispatcher import ToolDispatcher
from magi_agent.tools.manifest import RuntimeMode, ToolManifest, ToolSource
from magi_agent.tools.permission import ToolPermissionPolicy
from magi_agent.tools.read_ledger import (
    ReadLedger,
    ReadLedgerConfig,
    WorkspaceMutationReadCheck,
    safe_workspace_relative_path,
    workspace_content_digest,
)
from magi_agent.tools.registry import ToolRegistry
from magi_agent.tools.result import ToolResult


logger = logging.getLogger(__name__)

Gate5BFullToolHostStatus = Literal["disabled", "blocked", "ready"]
Gate5BFullToolOutcomeStatus = Literal["ok", "blocked", "error", "duplicate"]

_GATE5B_LEGACY_FULL_TOOLHOST_TOOL_NAMES = (
    "Clock",
    "Calculation",
    "FileRead",
    "Glob",
    "Grep",
    "FileWrite",
    "FileEdit",
    "PatchApply",
    "Bash",
)

_GATE5B_FIRST_PARTY_REGISTRY_TOOL_NAMES = (
    "AgentMemoryRemember",
    "AgentMemorySearch",
    "ArtifactCreate",
    "ArtifactDelete",
    "ArtifactList",
    "ArtifactRead",
    "ArtifactUpdate",
    "AskUserQuestion",
    "BatchRead",
    "Browser",
    "CodeDiagnostics",
    "CodeIntelligence",
    "CodeSymbolSearch",
    "CodeWorkspace",
    "CodingBenchmark",
    "CommitCheckpoint",
    "CronCreate",
    "CronDelete",
    "CronList",
    "CronUpdate",
    "DateRange",
    "DocumentWrite",
    "EnterPlanMode",
    "ExitPlanMode",
    "ExternalSourceCache",
    "ExternalSourceRead",
    "ExternalToolLoader",
    "GitDiff",
    "HealthStatus",
    "KnowledgeSearch",
    "KnowledgeWrite",
    "MemoryRedact",
    "MissionLedger",
    "NotifyUser",
    "PackageDependencyResolve",
    "ProjectVerificationPlanner",
    "RepoMap",
    "RepoTaskState",
    "RepositoryMap",
    "SafeCommand",
    "SkillLoader",
    "SkillRuntimeHooks",
    "SocialBrowser",
    "SpawnAgent",
    "SpawnWorktreeApply",
    "SpreadsheetWrite",
    "SwitchToActMode",
    "TaskBoard",
    "TaskGet",
    "TaskList",
    "TaskOutput",
    "TaskStop",
    "TaskWait",
    "TestRun",
    "ToolSearch",
    "WebFetch",
    "WebSearch",
)

GATE5B_FULL_TOOLHOST_TOOL_NAMES = (
    *_GATE5B_LEGACY_FULL_TOOLHOST_TOOL_NAMES,
    *_GATE5B_FIRST_PARTY_REGISTRY_TOOL_NAMES,
)

_LEGACY_TOOL_SOURCE = ToolSource(kind="builtin", package="openmagi.gate5b")
_LEGACY_TOOL_INPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": True,
}

_SAFE_ENVIRONMENTS = frozenset({"local", "development", "staging", "production"})
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SENSITIVE_PATH_PART_RE = re.compile(
    r"(^\.|(?:^|[-_.])(?:auth|config|cookie|credential|env|key|kube|kubeconfig|password|"
    r"secret|session|token)(?:[-_.]|$))",
    re.IGNORECASE,
)
_SENSITIVE_RE = re.compile(
    r"(?:"
    r"authorization\s*:\s*bearer\s+\S+|"
    r"\bbearer\s+\S+|"
    r"\bcookie\s*:\s*[^\n\r]+|"
    r"\bset-cookie\s*:\s*[^\n\r]+|"
    r"\bsid=[A-Za-z0-9._-]+|"
    r"\bsk-[A-Za-z0-9._-]+|"
    r"gh[opusr]_[A-Za-z0-9_]+|"
    r"github_pat_[A-Za-z0-9_]+|"
    r"xox[a-z]-[A-Za-z0-9._-]+|"
    r"AKIA[0-9A-Z]{8,}|"
    r"AIza[A-Za-z0-9_-]+|"
    r"/Users(?:/[^\s,;}\"']*)?|"
    r"/home(?:/[^\s,;}\"']*)?|"
    r"/workspace(?:/[^\s,;}\"']*)?|"
    r"/data/bots(?:/[^\s,;}\"']*)?|"
    r"raw[_ -]?(?:user|tool|session|auth|cookie|text)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought"
    r")",
    re.IGNORECASE,
)

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    arbitrary_types_allowed=True,
    hide_input_in_errors=True,
)


class Gate5BFullToolPathPolicyError(ValueError):
    pass


class Gate5BFullToolReadLedgerError(Exception):
    """Raised when read-before-edit enforcement blocks a workspace mutation.

    This is a *policy* signal, not a value error. It deliberately does NOT
    subclass ``ValueError`` so it cannot be accidentally swallowed by the broad
    ``except (OSError, ValueError, TypeError)`` fallthrough in ``dispatch`` (it
    must be caught explicitly to surface its model-actionable ``reason``).

    ``reason`` carries a stable, model-actionable code; the dispatch loop turns
    it into a ``blocked`` outcome with that reason so the model is told exactly
    what remediation is required:

    - ``read_ledger_no_prior_read``      -> read the file first before editing it
    - ``read_ledger_full_read_required`` -> read the whole file first before editing
    - ``read_ledger_stale_read_digest``  -> file changed since read; re-read it
    - ``read_ledger_file_disappeared_during_read``
          -> file vanished mid-mutation (TOCTOU); re-read and retry
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Gate5BFullToolRegistryBlocked(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_READ_LEDGER_SESSION_ID = "gate5b-read-ledger"
_READ_LEDGER_TURN_ID = "gate5b-turn"


class _Gate5BFullModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**values)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)


class Gate5BFullToolHostConfig(_Gate5BFullModel):
    enabled: bool = False
    kill_switch_enabled: bool = Field(default=True, alias="killSwitchEnabled")
    route_attachment_enabled: bool = Field(default=False, alias="routeAttachmentEnabled")
    selected_bot_digest: str = Field(default="", alias="selectedBotDigest")
    selected_owner_digest: str = Field(default="", alias="selectedOwnerDigest")
    environment: str = "local"
    environment_allowlist: tuple[str, ...] = Field(default=(), alias="environmentAllowlist")
    allowed_tool_names: tuple[str, ...] = Field(default=(), alias="allowedToolNames")
    max_tool_calls_per_turn: int = Field(default=0, ge=0, le=64, alias="maxToolCallsPerTurn")
    max_per_tool_output_bytes: int = Field(
        default=8192,
        ge=1,
        le=131072,
        alias="maxPerToolOutputBytes",
    )
    command_timeout_ms: int = Field(default=5000, ge=250, le=30000, alias="commandTimeoutMs")
    format_on_write_enabled: bool = Field(default=False, alias="formatOnWriteEnabled")
    lsp_diagnostics_enabled: bool = Field(default=False, alias="lspDiagnosticsEnabled")
    lsp_diagnostics_cap: int = Field(default=20, ge=1, le=100, alias="lspDiagnosticsCap")
    lsp_diagnostics_timeout_ms: int = Field(
        default=5000,
        ge=250,
        le=30000,
        alias="lspDiagnosticsTimeoutMs",
    )
    read_quality_enabled: bool = Field(default=False, alias="readQualityEnabled")
    read_max_lines: int = Field(default=2000, ge=1, le=100000, alias="readMaxLines")
    ripgrep_enabled: bool = Field(default=False, alias="ripgrepEnabled")

    @field_validator("environment_allowlist", "allowed_tool_names", mode="before")
    @classmethod
    def _coerce_tuple(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
            return tuple(str(item) for item in value)
        return ()

    @model_validator(mode="after")
    def _validate_metadata(self) -> Self:
        if self.environment not in _SAFE_ENVIRONMENTS:
            raise ValueError("Gate 5B full toolhost environment must be safe")
        for digest in (self.selected_bot_digest, self.selected_owner_digest):
            if digest and not _DIGEST_RE.fullmatch(digest):
                raise ValueError("Gate 5B full toolhost selected scope must use sha256 digests")
        for environment in self.environment_allowlist:
            if environment not in _SAFE_ENVIRONMENTS:
                raise ValueError("Gate 5B full toolhost env allowlist has unsafe values")
        return self


class Gate5BFullToolAttachmentFlags(_Gate5BFullModel):
    selected_full_toolhost_attached: bool = Field(
        default=False,
        alias="selectedFullToolhostAttached",
    )
    adk_function_tools_built: bool = Field(default=False, alias="adkFunctionToolsBuilt")
    route_attached: bool = Field(default=False, alias="routeAttached")
    production_attached: Literal[False] = Field(default=False, alias="productionAttached")
    production_workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="productionWorkspaceMutationAllowed",
    )
    workspace_mutation_allowed: bool = Field(default=False, alias="workspaceMutationAllowed")
    bash_command_allowed: bool = Field(default=False, alias="bashCommandAllowed")
    memory_write_allowed: Literal[False] = Field(default=False, alias="memoryWriteAllowed")
    browser_side_effect_allowed: Literal[False] = Field(
        default=False,
        alias="browserSideEffectAllowed",
    )
    channel_delivery_allowed: Literal[False] = Field(
        default=False,
        alias="channelDeliveryAllowed",
    )
    db_write_allowed: Literal[False] = Field(default=False, alias="dbWriteAllowed")


class Gate5BFullToolReceipt(_Gate5BFullModel):
    request_digest: str = Field(alias="requestDigest")
    tool_call_digest: str = Field(alias="toolCallDigest")
    tool_name: str = Field(alias="toolName")
    status: Gate5BFullToolOutcomeStatus
    bounded_output_digest: str = Field(alias="boundedOutputDigest")
    output_byte_count: int = Field(default=0, ge=0, alias="outputByteCount")

    @field_validator("request_digest", "tool_call_digest", "bounded_output_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if _DIGEST_RE.fullmatch(value):
            return value
        return _digest(value)


class Gate5BFullToolOutcome(_Gate5BFullModel):
    status: Gate5BFullToolOutcomeStatus
    reason: str
    receipt: Gate5BFullToolReceipt
    output_preview: object | None = Field(default=None, alias="outputPreview")
    handler_called: bool = Field(default=False, alias="handlerCalled")
    coding_mutation_receipt: CodingToolReceiptRecord | None = Field(
        default=None,
        alias="codingMutationReceipt",
    )
    code_diagnostics_receipt: CodeDiagnosticsRecord | None = Field(
        default=None,
        alias="codeDiagnosticsReceipt",
    )


class Gate5BFullToolBundle(_Gate5BFullModel):
    status: Gate5BFullToolHostStatus
    reason: str
    host: "Gate5BFullToolHost"
    tools: tuple[FunctionTool, ...] = ()
    exposed_tool_names: tuple[str, ...] = Field(default=(), alias="exposedToolNames")
    attachment_flags: Gate5BFullToolAttachmentFlags = Field(alias="attachmentFlags")
    workspace_root_digest: str = Field(alias="workspaceRootDigest")


class Gate5BFullToolCounter:
    def __init__(self, config: Gate5BFullToolHostConfig) -> None:
        self._config = config
        self._records: dict[tuple[str, str], Gate5BFullToolReceipt] = {}
        self._argument_digests: dict[tuple[str, str], str] = {}
        self._tool_calls = 0

    @property
    def receipt_count(self) -> int:
        return len(self._records)

    @property
    def receipts(self) -> tuple[Gate5BFullToolReceipt, ...]:
        return tuple(self._records.values())

    def before_call(
        self,
        *,
        request_digest: str,
        tool_call_digest: str,
        argument_digest: str,
        tool_name: str,
    ) -> Gate5BFullToolOutcome | None:
        key = (request_digest, tool_call_digest)
        existing = self._records.get(key)
        if existing is not None:
            if self._argument_digests.get(key) == argument_digest:
                return Gate5BFullToolOutcome(
                    status="duplicate",
                    reason="duplicate_tool_call",
                    receipt=existing,
                    outputPreview=None,
                    handlerCalled=False,
                )
            return Gate5BFullToolOutcome(
                status="blocked",
                reason="tool_call_digest_conflict",
                receipt=existing,
                outputPreview=None,
                handlerCalled=False,
            )
        if self._tool_calls >= self._config.max_tool_calls_per_turn:
            return self.blocked(
                request_digest=request_digest,
                tool_call_digest=tool_call_digest,
                tool_name=tool_name,
                reason="max_tool_calls_exhausted",
                argument_digest=argument_digest,
            )
        return None

    def finish_call(
        self,
        *,
        request_digest: str,
        tool_call_digest: str,
        argument_digest: str,
        tool_name: str,
        status: Gate5BFullToolOutcomeStatus,
        output_preview: object | None,
        output_byte_count: int,
        coding_mutation_receipt: CodingToolReceiptRecord | None = None,
        code_diagnostics_receipt: CodeDiagnosticsRecord | None = None,
    ) -> Gate5BFullToolOutcome:
        receipt = Gate5BFullToolReceipt(
            requestDigest=request_digest,
            toolCallDigest=tool_call_digest,
            toolName=tool_name,
            status=status,
            boundedOutputDigest=_digest(output_preview),
            outputByteCount=min(output_byte_count, self._config.max_per_tool_output_bytes),
        )
        self._records[(request_digest, tool_call_digest)] = receipt
        self._argument_digests[(request_digest, tool_call_digest)] = argument_digest
        self._tool_calls += 1
        return Gate5BFullToolOutcome(
            status=status,
            reason="tool_completed" if status == "ok" else "tool_error",
            receipt=receipt,
            outputPreview=output_preview,
            handlerCalled=True,
            codingMutationReceipt=coding_mutation_receipt,
            codeDiagnosticsReceipt=code_diagnostics_receipt,
        )

    def blocked(
        self,
        *,
        request_digest: str,
        tool_call_digest: str,
        tool_name: str,
        reason: str,
        argument_digest: str | None = None,
        record: bool = True,
    ) -> Gate5BFullToolOutcome:
        receipt = Gate5BFullToolReceipt(
            requestDigest=request_digest,
            toolCallDigest=tool_call_digest,
            toolName=tool_name,
            status="blocked",
            boundedOutputDigest=_digest({"blocked": reason}),
            outputByteCount=0,
        )
        # Most blocks are terminal and recorded so an identical retry is caught
        # by the dedup path. Ledger blocks are the exception: the model is
        # expected to read the file and RETRY the same call, so they must not be
        # recorded (and never counted toward the budget) or the retry would be
        # rejected as a duplicate/digest conflict and recovery would be
        # impossible.
        if record:
            key = (request_digest, tool_call_digest)
            self._records[key] = receipt
            self._argument_digests[key] = argument_digest or _digest({"blocked": reason})
        return Gate5BFullToolOutcome(
            status="blocked",
            reason=reason,
            receipt=receipt,
            outputPreview=None,
            handlerCalled=False,
        )


class Gate5BFullToolHost:
    def __init__(
        self,
        *,
        config: Gate5BFullToolHostConfig,
        workspace_root: Path,
        exposed_tool_names: tuple[str, ...],
        now_ms: Callable[[], int],
        tool_registry: ToolRegistry | None = None,
        read_ledger_enabled: bool = False,
        diagnostics_provider: DiagnosticsProvider | None = None,
    ) -> None:
        self.config = config
        self.workspace_root = workspace_root.resolve()
        self.exposed_tool_names = exposed_tool_names
        self.now_ms = now_ms
        self.counter = Gate5BFullToolCounter(config)
        self._tool_registry = tool_registry
        self._tool_dispatcher = ToolDispatcher(tool_registry) if tool_registry is not None else None
        self.receipt_boundary = CodingToolReceiptBoundary(
            CodingToolReceiptConfig(enabled=True)
        )
        # One ledger per host. A host lives for the lifetime of a selected
        # bundle (one per turn/session attach), and ``dispatch`` is called many
        # times on the same instance, so a full read recorded by one FileRead
        # call is visible to a later FileWrite/FileEdit/PatchApply call in the
        # same host. Default off => behaviour identical to before.
        self.read_ledger = ReadLedger(
            ReadLedgerConfig(
                enabled=read_ledger_enabled,
                localInMemoryEnabled=read_ledger_enabled,
            )
        )
        # Stable, non-private workspace ref derived from the resolved root.
        self._read_ledger_workspace_ref = "workspace:" + _digest(
            str(self.workspace_root)
        )[7:31]
        self.diagnostics_boundary = CodeDiagnosticsBoundary(
            enabled=config.lsp_diagnostics_enabled
        )
        # Lazy-created real LSP client; only built when the flag is on and no
        # provider was injected (tests inject a fake provider for determinism).
        self._diagnostics_provider = diagnostics_provider
        self._owns_lsp_client = False

    def _resolve_diagnostics_provider(self) -> DiagnosticsProvider | None:
        if not self.config.lsp_diagnostics_enabled:
            return None
        if self._diagnostics_provider is None:
            self._diagnostics_provider = LspClient(
                self.workspace_root,
                timeout_s=self.config.lsp_diagnostics_timeout_ms / 1000,
            )
            self._owns_lsp_client = True
        return self._diagnostics_provider

    def shutdown(self) -> None:
        if self._owns_lsp_client and isinstance(self._diagnostics_provider, LspClient):
            self._diagnostics_provider.shutdown_all()

    async def dispatch(
        self,
        tool_name: str,
        arguments: Mapping[str, object] | None = None,
        *,
        request_digest: str,
        tool_call_id: str,
    ) -> Gate5BFullToolOutcome:
        args = dict(arguments or {})
        tool_call_digest = _digest({"tool": tool_name, "id": tool_call_id})
        argument_digest = _digest(args)
        preflight = self.counter.before_call(
            request_digest=request_digest,
            tool_call_digest=tool_call_digest,
            argument_digest=argument_digest,
            tool_name=tool_name,
        )
        if preflight is not None:
            return preflight
        if tool_name not in self.exposed_tool_names:
            return self.counter.blocked(
                request_digest=request_digest,
                tool_call_digest=tool_call_digest,
                tool_name=tool_name,
                reason="tool_not_allowlisted",
                argument_digest=argument_digest,
            )
        try:
            self._preflight_legacy_tool(tool_name, args, tool_call_id=tool_call_id)
            output = await self._handle(tool_name, args, tool_call_id=tool_call_id)
            # The diagnostics collection does blocking stdio reads against the
            # language server. Run it off the event loop so a slow/hung server
            # can't stall concurrent requests (mirrors the to_thread pattern in
            # hooks/bus.py and storage/session_store.py). Fail-open downstream.
            diagnostics_receipt = await asyncio.to_thread(
                self._after_write_diagnostics, tool_name, args, output
            )
            result = ToolResult(status="ok", output=output)
            coding_receipt = self.receipt_boundary.extract_receipt(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments=args,
                result=result,
            )
            return self.counter.finish_call(
                request_digest=request_digest,
                tool_call_digest=tool_call_digest,
                argument_digest=argument_digest,
                tool_name=tool_name,
                status="ok",
                output_preview=_bounded_output(output, self.config.max_per_tool_output_bytes),
                output_byte_count=_encoded_len(output),
                coding_mutation_receipt=coding_receipt,
                code_diagnostics_receipt=diagnostics_receipt,
            )
        except Gate5BFullToolRegistryBlocked as error:
            return self.counter.blocked(
                request_digest=request_digest,
                tool_call_digest=tool_call_digest,
                tool_name=tool_name,
                reason=error.reason,
                argument_digest=argument_digest,
            )
        except Gate5BFullToolReadLedgerError as ledger_error:
            # Ledger blocks are recoverable: read the file, then retry the SAME
            # call. Do not record the block, so the retry is not flagged as a
            # duplicate/digest conflict.
            return self.counter.blocked(
                request_digest=request_digest,
                tool_call_digest=tool_call_digest,
                tool_name=tool_name,
                reason=ledger_error.reason,
                record=False,
            )
        except Gate5BFullToolPathPolicyError:
            return self.counter.blocked(
                request_digest=request_digest,
                tool_call_digest=tool_call_digest,
                tool_name=tool_name,
                reason="path_policy_denied",
                argument_digest=argument_digest,
            )
        except subprocess.TimeoutExpired:
            result = ToolResult(status="error", errorMessage="command_timeout")
            coding_receipt = self.receipt_boundary.extract_receipt(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments=args,
                result=result,
            )
            return self.counter.finish_call(
                request_digest=request_digest,
                tool_call_digest=tool_call_digest,
                argument_digest=argument_digest,
                tool_name=tool_name,
                status="error",
                output_preview={"error": "command_timeout"},
                output_byte_count=0,
                coding_mutation_receipt=coding_receipt,
            )
        except (OSError, ValueError, TypeError):
            result = ToolResult(status="error", errorMessage="tool_error")
            coding_receipt = self.receipt_boundary.extract_receipt(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments=args,
                result=result,
            )
            return self.counter.finish_call(
                request_digest=request_digest,
                tool_call_digest=tool_call_digest,
                argument_digest=argument_digest,
                tool_name=tool_name,
                status="error",
                output_preview={"error": "tool_error"},
                output_byte_count=0,
                coding_mutation_receipt=coding_receipt,
            )

    def _ripgrep_active(self) -> bool:
        if not self.config.ripgrep_enabled:
            return False
        from magi_agent.coding.ripgrep import rg_available

        return rg_available()

    async def _handle(
        self,
        tool_name: str,
        args: Mapping[str, object],
        *,
        tool_call_id: str,
    ) -> object:
        if tool_name == "Clock":
            return {"nowMs": self.now_ms()}
        if tool_name == "Calculation":
            return {"value": _evaluate_expression(str(args.get("expression", "0")))}
        if tool_name == "FileRead":
            return self._handle_file_read(args)
        if tool_name == "Glob":
            pattern = str(args.get("pattern", "*"))
            if self._ripgrep_active():
                rg_matches = _ripgrep_glob(
                    self.workspace_root,
                    pattern,
                    limit=100,
                    timeout_s=self.config.command_timeout_ms / 1000,
                )
                if rg_matches is not None:
                    return {"matches": rg_matches}
            return {"matches": _safe_glob_files(self.workspace_root, pattern, limit=100)}
        if tool_name == "Grep":
            pattern = str(args.get("pattern", ""))
            matches: list[dict[str, object]] = []
            if not pattern:
                return {"matches": matches}
            glob = str(args.get("glob", "**/*"))
            if self._ripgrep_active():
                rg_matches = _ripgrep_grep(
                    self.workspace_root,
                    pattern,
                    glob,
                    limit=100,
                    timeout_s=self.config.command_timeout_ms / 1000,
                )
                if rg_matches is not None:
                    return {"matches": rg_matches}
            for relative in _safe_glob_files(self.workspace_root, glob, limit=200):
                path = self.workspace_root / relative
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if pattern in text:
                    matches.append({"path": relative, "digest": _digest(text)})
                    if len(matches) >= 50:
                        break
            return {"matches": matches}
        if tool_name == "FileWrite":
            target = _safe_child_path(
                self.workspace_root,
                str(args.get("path", "")),
                allow_missing=True,
            )
            self._enforce_read_before_mutation(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            content = str(args.get("content", ""))
            target.write_text(content, encoding="utf-8")
            self._format_after_write(target)
            result: dict[str, object] = {
                "pathDigest": _digest(target.relative_to(self.workspace_root).as_posix()),
                "bytesWritten": len(content.encode("utf-8")),
            }
            if self.config.format_on_write_enabled:
                content_digest = self._content_digest(target)
                if content_digest is not None:
                    result["contentDigest"] = content_digest
            return result
        if tool_name == "FileEdit":
            target = _safe_child_path(self.workspace_root, str(args.get("path", "")))
            self._enforce_read_before_mutation(target)
            old_text = str(args.get("oldText", args.get("old_text", "")))
            new_text = str(args.get("newText", args.get("new_text", "")))
            if not old_text:
                raise ValueError("empty_old_text")
            current = target.read_text(encoding="utf-8", errors="replace")
            if _EDIT_FUZZY_MATCH_ENABLED:
                from magi_agent.coding.edit_matching import (
                    MultipleMatchesError as _MultipleMatchesError,
                    NoMatchError as _NoMatchError,
                    replace as _fuzzy_replace,
                )
                try:
                    result_text = _fuzzy_replace(current, old_text, new_text)
                except _NoMatchError:
                    raise ValueError("old_text_not_found")
                except _MultipleMatchesError:
                    raise ValueError("old_text_not_unique")
                target.write_text(result_text, encoding="utf-8")
            else:
                if old_text not in current:
                    raise ValueError("old_text_not_found")
                target.write_text(current.replace(old_text, new_text, 1), encoding="utf-8")
            self._format_after_write(target)
            edit_result: dict[str, object] = {
                "pathDigest": _digest(target.relative_to(self.workspace_root).as_posix()),
                "replacements": 1,
            }
            if self.config.format_on_write_enabled:
                content_digest = self._content_digest(target)
                if content_digest is not None:
                    edit_result["contentDigest"] = content_digest
            return edit_result
        if tool_name == "PatchApply":
            target = _safe_child_path(
                self.workspace_root,
                str(args.get("path", "")),
                allow_missing=True,
            )
            self._enforce_read_before_mutation(target)
            if "content" in args:
                content = str(args.get("content", ""))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                self._format_after_write(target)
                patch_result: dict[str, object] = {
                    "pathDigest": _digest(target.relative_to(self.workspace_root).as_posix()),
                    "patchMode": "content_replace",
                    "bytesWritten": len(content.encode("utf-8")),
                }
                if self.config.format_on_write_enabled:
                    content_digest = self._content_digest(target)
                    if content_digest is not None:
                        patch_result["contentDigest"] = content_digest
                return patch_result
            raise ValueError("unsupported_patch_shape")
        if tool_name == "Bash":
            command = str(args.get("command", "")).strip()
            if not command:
                raise ValueError("empty_command")
            completed = subprocess.run(
                command,
                cwd=self.workspace_root,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.config.command_timeout_ms / 1000,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                check=False,
            )
            stdout = _redact(completed.stdout)[0 : self.config.max_per_tool_output_bytes]
            stderr = _redact(completed.stderr)[0 : self.config.max_per_tool_output_bytes]
            return {
                "exitCode": completed.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "stdoutDigest": _digest(completed.stdout),
                "stderrDigest": _digest(completed.stderr),
            }
        return await self._dispatch_registry_tool(tool_name, args, tool_call_id=tool_call_id)

    async def _dispatch_registry_tool(
        self,
        tool_name: str,
        args: Mapping[str, object],
        *,
        tool_call_id: str,
    ) -> object:
        if self._tool_registry is None or self._tool_dispatcher is None:
            raise ValueError("unsupported_tool")
        manifest = self._tool_registry.resolve_enabled(tool_name)
        if manifest is None or manifest.adk_tool_type != "FunctionTool":
            raise ValueError("unsupported_tool")
        mode: Literal["plan", "act"] = (
            "act" if "act" in manifest.available_in_modes else "plan"
        )
        result = await self._tool_dispatcher.dispatch(
            tool_name,
            dict(args),
            ToolContext(
                botId="gate5b-selected-full-toolhost",
                turnId=f"gate5b-full-toolhost:{tool_call_id}",
                workspaceRoot=str(self.workspace_root),
                permissionScope={
                    "mode": "selected_full_toolhost",
                    "source": "selected_full_toolhost",
                },
            ),
            mode=mode,
            exposed_tool_names=self.exposed_tool_names,
        )
        if result.status == "ok":
            return result.model_dump(by_alias=True, mode="json", warnings=False)
        if result.status in {"blocked", "needs_approval"}:
            raise Gate5BFullToolRegistryBlocked(
                _safe_reason_label(
                    result.error_code
                    or str(result.metadata.get("reason") or result.status)
                )
            )
        raise ValueError(_safe_reason_label(result.error_code or "tool_error"))

    def _preflight_legacy_tool(
        self,
        tool_name: str,
        args: Mapping[str, object],
        *,
        tool_call_id: str,
    ) -> None:
        if tool_name not in _GATE5B_LEGACY_FULL_TOOLHOST_TOOL_NAMES:
            return
        preflight_tool_name = tool_name
        if (
            tool_name == "PatchApply"
            and "content" in args
            and "patch" not in args
            and "diff" not in args
        ):
            preflight_tool_name = "FileWrite"
        manifest = _legacy_tool_manifest(preflight_tool_name)
        mode: RuntimeMode = "act" if "act" in manifest.available_in_modes else "plan"
        decision = ToolPermissionPolicy().decide(
            manifest,
            dict(args),
            ToolContext(
                botId="gate5b-selected-full-toolhost",
                turnId=f"gate5b-full-toolhost:{tool_call_id}",
                workspaceRoot=str(self.workspace_root),
                permissionScope={
                    "mode": "selected_full_toolhost",
                    "source": "selected_full_toolhost",
                },
            ),
            mode=mode,
        )
        if decision.action == "allow":
            return
        raise Gate5BFullToolRegistryBlocked(_permission_reason_code(decision.metadata))

    def _workspace_relative(self, target: Path) -> str | None:
        try:
            return safe_workspace_relative_path(
                target.relative_to(self.workspace_root).as_posix()
            )
        except ValueError:
            return None

    def _record_full_read(self, target: Path, content: str) -> None:
        """Record a full read into the session-scoped ledger (no-op when off)."""

        if not self.read_ledger.config.enabled:
            return
        relative = self._workspace_relative(target)
        if relative is None:
            return
        try:
            stat = target.stat()
            size_bytes = max(stat.st_size, 0)
            mtime_ns = max(stat.st_mtime_ns, 0)
        except OSError:
            size_bytes = len(content.encode("utf-8"))
            mtime_ns = 0
        try:
            self.read_ledger.record_read(
                session_id=_READ_LEDGER_SESSION_ID,
                workspace_ref=self._read_ledger_workspace_ref,
                path=relative,
                digest=workspace_content_digest(content),
                size_bytes=size_bytes,
                mtime_ns=mtime_ns,
                read_mode="full",
                turn_id=_READ_LEDGER_TURN_ID,
                tool_use_id="gate5b-file-read",
            )
        except ValidationError:
            # Unsafe/sealed/private-ref paths fail ledger entry validation and
            # cannot be recorded; the mutation preflight still independently
            # blocks them, so skipping the record here is safe.
            return
        except ValueError:
            # Any other ValueError (e.g. an unexpected path normalization edge
            # from safe_workspace_relative_path) would silently drop the record
            # and later cause a false ``no_prior_read``. Log so the skip is
            # observable, then preserve fail-open behaviour.
            logger.debug(
                "gate5b read-ledger skipped recording full read for %r",
                relative,
                exc_info=True,
            )
            return

    def _enforce_read_before_mutation(self, target: Path) -> None:
        """Block edits/overwrites of existing files lacking a fresh full read.

        Creating a brand-new file (no current on-disk content) is exempt.

        Sensitive/sealed/secret path policy is NOT handled here: it runs earlier
        in ``_safe_child_path`` (sealed basenames, dot-paths, secret patterns)
        and in the ledger's own ``is_unsafe_workspace_path`` guard. The ledger
        only adds the read-before-edit requirement on top of those checks and
        never weakens them.
        """

        if not self.read_ledger.config.enabled:
            return
        relative = self._workspace_relative(target)
        if relative is None:
            # Path policy will reject elsewhere; do not weaken those checks.
            return
        if target.is_file():
            try:
                current_text = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                # TOCTOU: the file vanished between the is_file() probe and the
                # read used to compute its current digest. Surface a clear,
                # model-actionable block (re-read and retry) instead of letting
                # the OSError fall through to a generic ``tool_error``.
                raise Gate5BFullToolReadLedgerError(
                    "read_ledger_file_disappeared_during_read"
                ) from None
            current_digest: str | None = workspace_content_digest(current_text)
            mutation_kind = "edit"
        else:
            current_digest = None
            mutation_kind = "create"
        decision = self.read_ledger.require_fresh_full_read(
            WorkspaceMutationReadCheck(
                sessionId=_READ_LEDGER_SESSION_ID,
                workspaceRef=self._read_ledger_workspace_ref,
                path=relative,
                currentDigest=current_digest,
                mutationKind=mutation_kind,
            )
        )
        if decision.status == "ok":
            return
        primary = decision.reason_codes[0] if decision.reason_codes else "blocked"
        raise Gate5BFullToolReadLedgerError(f"read_ledger_{primary}")

    def _format_after_write(self, target: Path) -> None:
        """Run the matching formatter on a just-written file (flag-gated, fail-open).

        Mirrors OpenCode's format-after-edit: only files under the workspace
        root are formatted (already guaranteed by ``_safe_child_path``). A
        missing/failing/timed-out formatter never fails the write. Imported
        lazily so this new module is never pulled into import-boundary tests.
        """
        if not self.config.format_on_write_enabled:
            return
        from magi_agent.coding.formatter_runner import run_formatter

        try:
            run_formatter(
                target,
                timeout_seconds=self.config.command_timeout_ms / 1000,
                cwd=self.workspace_root,
            )
        except Exception:  # noqa: BLE001 - format step must never fail the write
            return

    def _content_digest(self, target: Path) -> str | None:
        """Return a content hash of the (possibly formatted) on-disk file.

        Only called from call sites that are already gated on
        ``self.config.format_on_write_enabled``.  Callers expose the result as
        ``contentDigest`` in the tool response (a field separate from
        ``pathDigest``), which is only present when the flag is ON.

        Returns ``None`` on an ``OSError`` reading the file (e.g. the formatter
        deleted it — extremely unlikely but fail-open).

        ``pathDigest`` is ALWAYS ``_digest(relpath)`` regardless of this flag —
        it identifies the path, not the content.  This ``contentDigest`` field
        is the formatted-content hash and is only present when the flag is ON.
        """
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        return _digest(content)

    def _after_write_diagnostics(
        self,
        tool_name: str,
        args: Mapping[str, object],
        output: object,
    ) -> CodeDiagnosticsRecord | None:
        """Run LSP diagnostics after a successful workspace mutation.

        Flag-gated and fully fail-open: any error (missing server, timeout,
        unreadable file, unsupported language) yields ``None`` and appends no
        diagnostics block, so the write is never failed by diagnostics work.

        On success with ERROR diagnostics it mutates *output* in place to carry
        an ``lspDiagnostics`` block (``<diagnostics file="...">...``) and
        returns a ``CodeDiagnosticsRecord`` for evidence emission.
        """
        provider = self._resolve_diagnostics_provider()
        if provider is None:
            return None
        if tool_name not in {"FileWrite", "FileEdit", "PatchApply"}:
            return None
        if not isinstance(output, dict):
            return None
        try:
            target = _safe_child_path(self.workspace_root, str(args.get("path", "")))
        except Gate5BFullToolPathPolicyError:
            return None
        if language_id_for_path(target) is None:
            return None
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        errors: list[Diagnostic] = collect_error_diagnostics(
            provider,
            target,
            text,
            cap=self.config.lsp_diagnostics_cap,
        )
        if not errors:
            return None
        relative = target.relative_to(self.workspace_root).as_posix()
        file_digest = _digest(relative)
        # Model-facing block uses the relative workspace path so the model
        # knows which file to fix (relative paths are already safe — gate5b's
        # _redact strips absolute prefixes like /Users, /home, /workspace).
        # The evidence record still uses the digest for public-safety.
        block = format_diagnostics_block(relative, errors)
        output["lspDiagnostics"] = (
            "LSP errors detected in this file, please fix:\n\n" + block
        )
        return self.diagnostics_boundary.build_record(
            checker=_diagnostics_checker_label(target),
            file_digest=file_digest,
            errors=errors,
            cap=self.config.lsp_diagnostics_cap,
        )

    def _handle_file_read(self, args: Mapping[str, object]) -> object:
        path_text = str(args.get("path", ""))
        if not self.config.read_quality_enabled:
            target = _safe_child_path(self.workspace_root, path_text)
            content = target.read_text(encoding="utf-8", errors="replace")
            self._record_full_read(target, content)
            return {
                "pathDigest": _digest(
                    target.relative_to(self.workspace_root).as_posix()
                ),
                "content": content[: self.config.max_per_tool_output_bytes],
            }

        from magi_agent.coding.read_format import (
            LINE_NUMBER_GUIDANCE,
            apply_caps,
            binary_file_message,
            did_you_mean,
            is_binary,
            number_lines,
        )

        # Missing-file handling first so we can offer "Did you mean?" suggestions
        # without leaking sealed/secret names (those raise before reaching here).
        try:
            target = _safe_child_path(self.workspace_root, path_text)
        except Gate5BFullToolPathPolicyError:
            relative = str(path_text or "").replace("\\", "/").strip().strip("/")
            basename = relative.rsplit("/", 1)[-1] if relative else relative
            # SECURITY: if the path is a workspace escape (contains ".." segments,
            # starts with "/" or "~") do NOT probe the filesystem outside the root
            # and do NOT build did-you-mean candidates — return an empty-suggestion
            # fileNotFound without touching anything outside workspace.
            if basename and not _is_gate5b_workspace_escape(path_text):
                if not (self.workspace_root / relative).exists():
                    suggestions = self._did_you_mean_candidates(
                        relative, basename, did_you_mean
                    )
                    if suggestions:
                        return {
                            "fileNotFound": True,
                            "path": relative,
                            "suggestions": suggestions,
                            "message": (
                                f"File not found: {relative}. "
                                f"Did you mean? {', '.join(suggestions)}"
                            ),
                        }
            raise

        path_digest = _digest(target.relative_to(self.workspace_root).as_posix())
        raw = target.read_bytes()
        if is_binary(raw[:8192]):
            return {
                "pathDigest": path_digest,
                "binary": True,
                "message": binary_file_message(),
            }

        text = raw.decode("utf-8", errors="replace")
        self._record_full_read(target, text)
        # Redaction MUST happen before numbering/caps so secrets never re-appear.
        redacted = _redact(text)
        offset = _read_offset(args.get("offset"))
        limit = _read_limit(args.get("limit"), self.config.read_max_lines)
        if offset > 1:
            redacted = "\n".join(redacted.split("\n")[offset - 1 :])
        capped, truncated, next_offset = apply_caps(
            redacted,
            max_lines=limit,
            max_bytes=self.config.max_per_tool_output_bytes,
            offset=offset,
        )
        footer = ""
        if truncated and next_offset is not None:
            marker = f"\n(truncated at line {next_offset}; use offset={next_offset} to continue)"
            if capped.endswith(marker):
                capped = capped[: -len(marker)]
            footer = marker
        numbered = number_lines(capped, offset=offset) + footer
        output: dict[str, object] = {
            "pathDigest": path_digest,
            "content": numbered,
            "truncated": truncated,
            "offset": offset,
            "lineNumberGuidance": LINE_NUMBER_GUIDANCE,
        }
        if next_offset is not None:
            output["nextOffset"] = next_offset
        return output

    def _did_you_mean_candidates(
        self,
        relative: str,
        basename: str,
        did_you_mean_fn: Callable[..., list[str]],
    ) -> list[str]:
        parent_rel = relative.rsplit("/", 1)[0] if "/" in relative else ""
        parent_dir = self.workspace_root / parent_rel if parent_rel else self.workspace_root
        # Defense-in-depth: resolve parent_dir and verify it is workspace_root or a
        # strict descendant before calling iterdir().  Any path that resolves outside
        # the workspace (e.g. via symlinks or residual ".." segments) gets an empty
        # result — we never traverse outside the workspace boundary.
        try:
            resolved_root = self.workspace_root.resolve(strict=False)
            resolved_parent = parent_dir.resolve(strict=False)
        except OSError:
            return []
        if resolved_root not in (resolved_parent, *resolved_parent.parents):
            return []
        try:
            entries = [
                entry.name
                for entry in parent_dir.iterdir()
                if entry.is_file()
                and not entry.is_symlink()
                and not _is_sensitive_workspace_path(
                    Path(f"{parent_rel}/{entry.name}" if parent_rel else entry.name)
                )
            ]
        except OSError:
            return []
        return did_you_mean_fn(entries, basename)


def _diagnostics_checker_label(path: Path) -> str:
    language_id = language_id_for_path(path)
    if language_id == "python":
        return "pyright"
    if language_id in {
        "typescript",
        "typescriptreact",
        "javascript",
        "javascriptreact",
    }:
        return "typescript-language-server"
    return "lsp"


def build_gate5b_full_toolhost_bundle(
    *,
    config: Gate5BFullToolHostConfig | Mapping[str, object] | None = None,
    scope: Mapping[str, object] | None = None,
    workspace_root: str | Path,
    now_ms: Callable[[], int] | None = None,
    tool_registry: ToolRegistry | None = None,
    read_ledger_enabled: bool | None = None,
    diagnostics_provider: DiagnosticsProvider | None = None,
) -> Gate5BFullToolBundle:
    safe_config = Gate5BFullToolHostConfig.model_validate(config or {})
    workspace = Path(workspace_root)
    selected_scope_error = _selected_scope_error(safe_config, scope or {}, workspace)
    exposed = (
        _selected_tool_names(safe_config.allowed_tool_names)
        if selected_scope_error is None
        else ()
    )
    if read_ledger_enabled is None:
        read_ledger_enabled = _read_ledger_enabled_from_env()
    host = Gate5BFullToolHost(
        config=safe_config,
        workspace_root=workspace,
        exposed_tool_names=exposed,
        now_ms=now_ms or _now_ms,
        tool_registry=tool_registry,
        read_ledger_enabled=read_ledger_enabled,
        diagnostics_provider=diagnostics_provider,
    )
    if selected_scope_error is not None:
        return Gate5BFullToolBundle(
            status="disabled" if selected_scope_error == "gate_disabled" else "blocked",
            reason=selected_scope_error,
            host=host,
            tools=(),
            exposedToolNames=(),
            attachmentFlags=Gate5BFullToolAttachmentFlags(),
            workspaceRootDigest=_digest(str(host.workspace_root)),
        )
    tools = tuple(_build_adk_tool(host, name) for name in exposed)
    return Gate5BFullToolBundle(
        status="ready",
        reason="selected_full_toolhost_ready",
        host=host,
        tools=tools,
        exposedToolNames=exposed,
        attachmentFlags=Gate5BFullToolAttachmentFlags(
            selectedFullToolhostAttached=True,
            adkFunctionToolsBuilt=bool(tools),
            routeAttached=safe_config.route_attachment_enabled,
            workspaceMutationAllowed=True,
            bashCommandAllowed="Bash" in exposed,
        ),
        workspaceRootDigest=_digest(str(host.workspace_root)),
    )


def _selected_scope_error(
    config: Gate5BFullToolHostConfig,
    scope: Mapping[str, object],
    workspace_root: Path,
) -> str | None:
    if not config.enabled:
        return "gate_disabled"
    if config.kill_switch_enabled:
        return "kill_switch_enabled"
    if not config.route_attachment_enabled:
        return "route_attachment_disabled"
    if scope.get("selectedBotDigest") != config.selected_bot_digest:
        return "bot_not_selected"
    if scope.get("selectedOwnerDigest") != config.selected_owner_digest:
        return "owner_not_selected"
    if scope.get("environment") != config.environment:
        return "environment_not_selected"
    if config.environment not in config.environment_allowlist:
        return "environment_not_allowlisted"
    if config.max_tool_calls_per_turn <= 0:
        return "tool_call_budget_disabled"
    try:
        workspace_root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return "workspace_root_unavailable"
    if not workspace_root.is_dir():
        return "workspace_root_unavailable"
    return None


def _selected_tool_names(names: Sequence[str]) -> tuple[str, ...]:
    allowed = set(names)
    return tuple(name for name in GATE5B_FULL_TOOLHOST_TOOL_NAMES if name in allowed)


def _legacy_tool_manifest(tool_name: str) -> ToolManifest:
    if tool_name in {"Clock", "Calculation"}:
        return _legacy_manifest(
            tool_name,
            permission="meta",
            modes=("plan", "act"),
            tags=("utility", "meta"),
            parallel_safe=True,
        )
    if tool_name in {"FileRead", "Glob", "Grep"}:
        return _legacy_manifest(
            tool_name,
            permission="read",
            modes=("plan", "act"),
            tags=("workspace", "read"),
            parallel_safe=True,
        )
    if tool_name in {"FileWrite", "FileEdit", "PatchApply"}:
        return _legacy_manifest(
            tool_name,
            permission="write",
            modes=("act",),
            tags=("workspace", "write"),
            mutates_workspace=True,
        )
    if tool_name == "Bash":
        return _legacy_manifest(
            tool_name,
            permission="execute",
            modes=("act",),
            tags=("workspace", "command", "execute", "requires-approval"),
            dangerous=True,
            mutates_workspace=True,
        )
    raise ValueError("unsupported_legacy_tool")


def _legacy_manifest(
    name: str,
    *,
    permission: str,
    modes: tuple[RuntimeMode, ...],
    tags: tuple[str, ...],
    dangerous: bool = False,
    mutates_workspace: bool = False,
    parallel_safe: bool = False,
) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=f"Gate 5B selected full toolhost {name} tool.",
        kind="core",
        source=_LEGACY_TOOL_SOURCE,
        permission=permission,  # type: ignore[arg-type]
        inputSchema=_LEGACY_TOOL_INPUT_SCHEMA,
        timeoutMs=120_000,
        availableInModes=modes,
        tags=tags,
        dangerous=dangerous,
        mutatesWorkspace=mutates_workspace,
        isConcurrencySafe=parallel_safe,
        parallelSafety="readonly" if parallel_safe else "unsafe",
        enabled_by_default=True,
        opt_out=True,
    )


def _permission_reason_code(metadata: Mapping[str, object]) -> str:
    reason_codes = metadata.get("reasonCodes")
    if isinstance(reason_codes, Sequence) and not isinstance(reason_codes, str | bytes):
        for item in reason_codes:
            if isinstance(item, str) and item.strip():
                return _safe_reason_label(item)
    reason = metadata.get("reason")
    if isinstance(reason, str) and reason.strip():
        return _safe_reason_label(reason)
    return "tool_permission_blocked"


def _build_adk_tool(host: Gate5BFullToolHost, name: str) -> FunctionTool:
    if name == "Clock":

        async def invoke_clock(tool_context: object | None = None) -> dict[str, object]:
            return await _dispatch_adk_tool(host, "Clock", {}, tool_context)

        return _function_tool(name, invoke_clock)

    if name == "Calculation":

        async def invoke_calculation(
            expression: str = "0",
            tool_context: object | None = None,
        ) -> dict[str, object]:
            return await _dispatch_adk_tool(host, "Calculation", {"expression": expression}, tool_context)

        return _function_tool(name, invoke_calculation)

    if name == "FileRead":

        async def invoke_file_read(path: str = "", tool_context: object | None = None) -> dict[str, object]:
            return await _dispatch_adk_tool(host, "FileRead", {"path": path}, tool_context)

        return _function_tool(name, invoke_file_read)

    if name == "Glob":

        async def invoke_glob(pattern: str = "*", tool_context: object | None = None) -> dict[str, object]:
            return await _dispatch_adk_tool(host, "Glob", {"pattern": pattern}, tool_context)

        return _function_tool(name, invoke_glob)

    if name == "Grep":

        async def invoke_grep(
            pattern: str = "",
            glob: str = "**/*",
            tool_context: object | None = None,
        ) -> dict[str, object]:
            return await _dispatch_adk_tool(host, "Grep", {"pattern": pattern, "glob": glob}, tool_context)

        return _function_tool(name, invoke_grep)

    if name == "FileWrite":

        async def invoke_file_write(
            path: str = "",
            content: str = "",
            tool_context: object | None = None,
        ) -> dict[str, object]:
            return await _dispatch_adk_tool(host, "FileWrite", {"path": path, "content": content}, tool_context)

        return _function_tool(name, invoke_file_write)

    if name == "FileEdit":

        async def invoke_file_edit(
            path: str = "",
            oldText: str = "",
            newText: str = "",
            tool_context: object | None = None,
        ) -> dict[str, object]:
            return await _dispatch_adk_tool(host, "FileEdit", {"path": path, "oldText": oldText, "newText": newText}, tool_context)

        return _function_tool(name, invoke_file_edit)

    if name == "PatchApply":

        async def invoke_patch_apply(
            path: str = "",
            content: str = "",
            patch: str = "",
            tool_context: object | None = None,
        ) -> dict[str, object]:
            arguments = {"path": path}
            if content:
                arguments["content"] = content
            if patch:
                arguments["patch"] = patch
            return await _dispatch_adk_tool(host, "PatchApply", arguments, tool_context)

        return _function_tool(name, invoke_patch_apply)

    if name == "Bash":

        async def invoke_bash(command: str = "", tool_context: object | None = None) -> dict[str, object]:
            return await _dispatch_adk_tool(host, "Bash", {"command": command}, tool_context)

        return _function_tool(name, invoke_bash)

    tool_name = name

    async def invoke_registry_tool(
        query: str = "",
        path: str = "",
        content: str = "",
        text: str = "",
        url: str = "",
        title: str = "",
        target: str = "",
        mode: str = "",
        command: str = "",
        pattern: str = "",
        glob: str = "",
        expression: str = "",
        id: str = "",
        tool_context: object | None = None,
    ) -> dict[str, object]:
        arguments = _registry_adk_arguments(
            query=query,
            path=path,
            content=content,
            text=text,
            url=url,
            title=title,
            target=target,
            mode=mode,
            command=command,
            pattern=pattern,
            glob=glob,
            expression=expression,
            id=id,
        )
        return await _dispatch_adk_tool(host, tool_name, arguments, tool_context)

    return _function_tool(name, invoke_registry_tool)


def _registry_adk_arguments(**values: str) -> dict[str, object]:
    return {
        key: value
        for key, value in values.items()
        if isinstance(value, str) and value.strip()
    }


async def _dispatch_adk_tool(
    host: Gate5BFullToolHost,
    name: str,
    arguments: Mapping[str, object],
    tool_context: object | None,
) -> dict[str, object]:
    del tool_context
    args = dict(arguments)
    outcome = await host.dispatch(
        name,
        args,
        request_digest=_digest({"tool": name, "arguments": args}),
        tool_call_id=f"adk:{name}:{_digest(args)[7:23]}",
    )
    return outcome.model_dump(by_alias=True, mode="json", warnings=False)


def _function_tool(name: str, func: Callable[..., object]) -> FunctionTool:
    func.__name__ = name
    func.__doc__ = f"Gate 5B selected full toolhost {name} tool."
    return FunctionTool(func, require_confirmation=False)


def _read_offset(value: object) -> int:
    if isinstance(value, bool):
        return 1
    if isinstance(value, int) and value >= 1:
        return value
    if isinstance(value, str) and value.strip().isdecimal():
        parsed = int(value.strip())
        return parsed if parsed >= 1 else 1
    return 1


def _read_limit(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value >= 1:
        return min(value, default)
    if isinstance(value, str) and value.strip().isdecimal():
        parsed = int(value.strip())
        return min(parsed, default) if parsed >= 1 else default
    return default


def _is_gate5b_workspace_escape(path_text: str) -> bool:
    """Return True if *path_text* attempts to escape the workspace.

    Matches paths that start with '/' or '~' (absolute / home-relative) or
    that contain any '..' segment after slash-normalisation.  Used to gate
    the did-you-mean branch so we never probe the filesystem outside the
    workspace root.
    """
    text = str(path_text or "").replace("\\", "/").strip()
    if not text or text.startswith(("/", "~")):
        return True
    parts = text.split("/")
    return any(part == ".." for part in parts)


def _safe_child_path(root: Path, path_text: str, *, allow_missing: bool = False) -> Path:
    normalized = str(path_text or "").replace("\\", "/").strip()
    if not normalized or normalized.startswith(("/", "~")):
        raise Gate5BFullToolPathPolicyError("unsafe path")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise Gate5BFullToolPathPolicyError("unsafe path")
    relative = Path(*parts)
    if _is_sensitive_workspace_path(relative):
        raise Gate5BFullToolPathPolicyError("protected path")
    candidate = (root / relative).resolve(strict=False)
    if root not in (candidate, *candidate.parents):
        raise Gate5BFullToolPathPolicyError("path escaped workspace")
    resolved_relative = Path() if candidate == root else candidate.relative_to(root)
    if ".git" in resolved_relative.parts:
        raise Gate5BFullToolRegistryBlocked("protected_git_path")
    if _is_sensitive_workspace_path(resolved_relative):
        raise Gate5BFullToolPathPolicyError("protected path")
    if not allow_missing and not candidate.is_file():
        raise Gate5BFullToolPathPolicyError("path is not readable file")
    parent = candidate.parent.resolve(strict=False)
    if root not in (parent, *parent.parents):
        raise Gate5BFullToolPathPolicyError("path escaped workspace")
    resolved_parent_relative = Path() if parent == root else parent.relative_to(root)
    if ".git" in resolved_parent_relative.parts:
        raise Gate5BFullToolRegistryBlocked("protected_git_path")
    if _is_sensitive_workspace_path(resolved_parent_relative):
        raise Gate5BFullToolPathPolicyError("protected path")
    return candidate


def _is_sensitive_workspace_path(relative_path: Path) -> bool:
    for part in relative_path.parts:
        if not part or part in {".", ".."}:
            return True
        if part.startswith("."):
            return True
        if _SENSITIVE_PATH_PART_RE.search(part):
            return True
    return False


def _safe_glob_files(root: Path, pattern: str, *, limit: int) -> list[str]:
    normalized = str(pattern or "*").replace("\\", "/").strip() or "*"
    if normalized.startswith(("/", "~")) or ".." in normalized.split("/"):
        return []
    matches: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current_dir = Path(dirpath)
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if not (current_dir / dirname).is_symlink()
        ]
        for filename in sorted(filenames):
            candidate = current_dir / filename
            if candidate.is_symlink():
                continue
            try:
                relative_path = candidate.relative_to(root)
            except ValueError:
                continue
            if _is_sensitive_workspace_path(relative_path):
                continue
            relative = relative_path.as_posix()
            if fnmatch.fnmatchcase(relative, normalized) or (
                normalized.startswith("**/")
                and fnmatch.fnmatchcase(relative, normalized[3:])
            ):
                matches.append(relative)
                if len(matches) >= limit:
                    return matches
    return matches


def _ripgrep_safe_relative(root: Path, raw: str) -> Path | None:
    """Validate an rg-reported relative path against workspace policy.

    Mirrors the guards in ``_safe_glob_files`` / ``_safe_child_path``: rejects
    escapes, symlinked components, and sealed/secret paths. ``rg`` can surface
    hidden files this policy still blocks, so every rg result passes through
    here before it is returned.
    """

    normalized = str(raw or "").replace("\\", "/").strip()
    if not normalized or normalized.startswith(("/", "~")):
        return None
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        return None
    relative_path = Path(*parts)
    if _is_sensitive_workspace_path(relative_path):
        return None
    candidate = (root / relative_path).resolve(strict=False)
    if root not in (candidate, *candidate.parents):
        return None
    if not candidate.is_file() or candidate.is_symlink():
        return None
    return candidate


def _mtime_sort_desc(root: Path, relatives: list[str], *, limit: int) -> list[str]:
    """Stat each path and return up to ``limit`` sorted by mtime descending."""

    stamped: list[tuple[float, str]] = []
    for relative in relatives:
        try:
            mtime = (root / relative).stat().st_mtime
        except OSError:
            continue
        stamped.append((mtime, relative))
    stamped.sort(key=lambda item: (-item[0], item[1]))
    return [relative for _, relative in stamped[:limit]]


def _ripgrep_glob(
    root: Path, pattern: str, *, limit: int, timeout_s: float
) -> list[str] | None:
    from magi_agent.coding.ripgrep import rg_files

    glob = _ripgrep_glob_arg(pattern)
    raw = rg_files(str(root), glob, limit=limit, timeout_s=timeout_s)
    safe: list[str] = []
    for item in raw:
        candidate = _ripgrep_safe_relative(root, item)
        if candidate is None:
            continue
        safe.append(candidate.relative_to(root).as_posix())
    return _mtime_sort_desc(root, safe, limit=limit)


def _ripgrep_grep(
    root: Path, pattern: str, glob: str, *, limit: int, timeout_s: float
) -> list[dict[str, object]] | None:
    from magi_agent.coding.ripgrep import rg_search

    glob_arg = _ripgrep_glob_arg(glob)
    raw = rg_search(str(root), pattern, glob_arg, limit=limit, timeout_s=timeout_s)
    seen: dict[str, Path] = {}
    for match in raw:
        if match.path in seen:
            continue
        candidate = _ripgrep_safe_relative(root, match.path)
        if candidate is None:
            continue
        seen[match.path] = candidate
    ordered = _mtime_sort_desc(root, list(seen.keys()), limit=limit)
    results: list[dict[str, object]] = []
    for relative in ordered:
        candidate = seen[relative]
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        results.append({"path": relative, "digest": _digest(text)})
    return results


def _ripgrep_glob_arg(pattern: str) -> str | None:
    """Map the toolhost glob arg to an rg ``--glob`` value (None == all files)."""

    normalized = str(pattern or "").replace("\\", "/").strip()
    if not normalized or normalized in {"*", "**", "**/*"}:
        return None
    return normalized


def _evaluate_expression(expression: str) -> int | float:
    import ast

    tree = ast.parse(expression, mode="eval")
    return _eval_ast(tree.body)


def _eval_ast(node: object) -> int | float:
    import ast

    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval_ast(node.operand)
    if isinstance(node, ast.BinOp):
        left = _eval_ast(node.left)
        right = _eval_ast(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        if isinstance(node.op, ast.Mod):
            return left % right
    raise ValueError("unsupported calculation expression")


def _bounded_output(value: object, max_bytes: int) -> object:
    sanitized = _sanitize_output(value)
    encoded = json.dumps(
        sanitized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=repr,
    ).encode("utf-8")
    if len(encoded) <= max_bytes:
        return sanitized
    return {"truncated": True, "digest": _digest(sanitized)}


def _sanitize_output(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key)[:80]: _sanitize_output(item)
            for key, item in value.items()
            if not _SENSITIVE_PATH_PART_RE.search(str(key))
        }
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        return [_sanitize_output(item) for item in value[:64]]
    if isinstance(value, str):
        return _redact(value)
    return value


def _redact(value: str) -> str:
    return _SENSITIVE_RE.sub("[redacted]", value)


def _encoded_len(value: object) -> int:
    return len(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=repr).encode(
            "utf-8"
        )
    )


def _digest(value: object) -> str:
    material = json.dumps(value, sort_keys=True, default=repr, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _safe_reason_label(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", value.strip().lower())
    return normalized[:80] or "tool_error"


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


def _read_ledger_enabled_from_env() -> bool:
    # Single source of truth lives in config.env; imported lazily here to keep
    # gate5b import boundaries unchanged.
    from magi_agent.config.env import is_read_ledger_enabled

    return is_read_ledger_enabled(os.environ)


__all__ = [
    "GATE5B_FULL_TOOLHOST_TOOL_NAMES",
    "Gate5BFullToolBundle",
    "Gate5BFullToolHost",
    "Gate5BFullToolHostConfig",
    "Gate5BFullToolOutcome",
    "Gate5BFullToolReadLedgerError",
    "build_gate5b_full_toolhost_bundle",
]
