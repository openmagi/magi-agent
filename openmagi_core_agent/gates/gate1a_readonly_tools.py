from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Self

from google.adk.tools import FunctionTool
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openmagi_core_agent.tools.catalog import register_core_tool_manifests
from openmagi_core_agent.tools.context import ToolContext
from openmagi_core_agent.tools.manifest import (
    Budget,
    PermissionClass,
    RuntimeMode,
    ToolManifest,
    ToolSource,
)
from openmagi_core_agent.tools.registry import ToolRegistry


Gate1AStatus = Literal["disabled", "blocked", "ready"]
Gate1AOutcomeStatus = Literal["ok", "blocked", "error", "duplicate"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    arbitrary_types_allowed=True,
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_ENVIRONMENTS = frozenset({"local", "development", "staging", "production"})
_READONLY_TOOL_ORDER = (
    "Clock",
    "Calculation",
    "FileRead",
    "Glob",
    "Grep",
    "GitDiff",
    "ArtifactList",
    "ArtifactRead",
    "HealthStatus",
    "TaskList",
    "TaskGet",
    "TaskOutput",
    "CronList",
)
GATE1A_READONLY_TOOL_NAMES = _READONLY_TOOL_ORDER
_FORBIDDEN_TOOL_NAMES = frozenset(
    {
        "Bash",
        "TestRun",
        "FileWrite",
        "FileEdit",
        "PatchApply",
        "ApplyPatch",
        "Delete",
        "FileDelete",
        "CronCreate",
        "CronUpdate",
        "CronDelete",
        "TaskStop",
        "TaskCreate",
        "TaskWait",
        "MemoryWrite",
        "BrowserOpen",
        "BrowserClick",
        "BrowserFill",
        "BrowserScroll",
        "TelegramSend",
        "DiscordSend",
        "FileDeliver",
        "FileSend",
        "WorkspaceMutate",
    }
)
GATE1A_FORBIDDEN_TOOL_NAMES = tuple(sorted(_FORBIDDEN_TOOL_NAMES))
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
_SENSITIVE_KEY_RE = re.compile(
    r"(authorization|auth|bearer|cookie|credential|key|password|path|private|raw|secret|session|token)",
    re.IGNORECASE,
)
_SENSITIVE_PATH_PART_RE = re.compile(
    r"(^\.|(?:^|[-_.])(?:auth|config|cookie|credential|env|key|kube|kubeconfig|password|"
    r"secret|session|token)(?:[-_.]|$))",
    re.IGNORECASE,
)


class Gate1APathPolicyError(ValueError):
    pass


class _Gate1AModel(BaseModel):
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


class Gate1AReadOnlyToolConfig(_Gate1AModel):
    enabled: bool = False
    kill_switch_enabled: bool = Field(default=True, alias="killSwitchEnabled")
    local_test_harness_enabled: bool = Field(default=False, alias="localTestHarnessEnabled")
    route_attachment_enabled: bool = Field(default=False, alias="routeAttachmentEnabled")
    selected_bot_digest: str = Field(default="", alias="selectedBotDigest")
    selected_owner_digest: str = Field(default="", alias="selectedOwnerDigest")
    environment: str = "local"
    environment_allowlist: tuple[str, ...] = Field(default=(), alias="environmentAllowlist")
    allowed_tool_names: tuple[str, ...] = Field(default=(), alias="allowedToolNames")
    max_tool_calls_per_turn: int = Field(default=0, ge=0, le=64, alias="maxToolCallsPerTurn")
    max_per_tool_output_bytes: int = Field(
        default=4096,
        ge=1,
        le=65536,
        alias="maxPerToolOutputBytes",
    )
    max_aggregate_output_bytes: int = Field(
        default=16384,
        ge=1,
        le=262144,
        alias="maxAggregateOutputBytes",
    )

    @field_validator("environment_allowlist", "allowed_tool_names", mode="before")
    @classmethod
    def _coerce_tuple(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
            return tuple(str(item) for item in value)
        return ()

    @model_validator(mode="after")
    def _validate_metadata(self) -> Self:
        if self.environment not in _SAFE_ENVIRONMENTS:
            raise ValueError("Gate 1A environment must be a known safe environment label")
        for digest in (self.selected_bot_digest, self.selected_owner_digest):
            if digest and not _DIGEST_RE.fullmatch(digest):
                raise ValueError("Gate 1A selected scope must use sha256 digests")
        for environment in self.environment_allowlist:
            if environment not in _SAFE_ENVIRONMENTS:
                raise ValueError("Gate 1A environment allowlist has unsafe values")
        return self


class Gate1AAttachmentFlags(_Gate1AModel):
    local_read_only_tools_attached: bool = Field(
        default=False,
        alias="localReadOnlyToolsAttached",
    )
    adk_function_tools_built: bool = Field(default=False, alias="adkFunctionToolsBuilt")
    route_attached: bool = Field(default=False, alias="routeAttached")
    production_attached: Literal[False] = Field(default=False, alias="productionAttached")
    user_visible_output_attached: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAttached",
    )
    write_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="writeMutationAllowed",
    )
    bash_command_allowed: Literal[False] = Field(default=False, alias="bashCommandAllowed")
    memory_write_allowed: Literal[False] = Field(default=False, alias="memoryWriteAllowed")
    browser_side_effect_allowed: Literal[False] = Field(
        default=False,
        alias="browserSideEffectAllowed",
    )
    telegram_discord_send_allowed: Literal[False] = Field(
        default=False,
        alias="telegramDiscordSendAllowed",
    )
    artifact_channel_delivery_allowed: Literal[False] = Field(
        default=False,
        alias="artifactChannelDeliveryAllowed",
    )
    production_transcript_sse_db_write_allowed: Literal[False] = Field(
        default=False,
        alias="productionTranscriptSseDbWriteAllowed",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_forbidden_flags_false(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data: dict[str, object] = {}
        for name, field in cls.model_fields.items():
            alias = field.alias or name
            if name in {
                "local_read_only_tools_attached",
                "adk_function_tools_built",
                "route_attached",
            }:
                data[alias] = bool(value.get(alias, value.get(name, False)))
            else:
                data[alias] = False
        return data


class Gate1AToolReceipt(_Gate1AModel):
    request_digest: str = Field(alias="requestDigest")
    tool_call_digest: str = Field(alias="toolCallDigest")
    allowed_tool_name: str = Field(alias="allowedToolName")
    status: Gate1AOutcomeStatus
    bounded_output_digest: str = Field(alias="boundedOutputDigest")
    output_byte_count: int = Field(default=0, ge=0, alias="outputByteCount")
    redaction_proof: Literal["redacted", "no_redaction_needed"] = Field(
        alias="redactionProof",
    )

    @field_validator("request_digest", "tool_call_digest", "bounded_output_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if _DIGEST_RE.fullmatch(value):
            return value
        return _digest(value)


class Gate1AToolOutcome(_Gate1AModel):
    status: Gate1AOutcomeStatus
    reason: str
    receipt: Gate1AToolReceipt
    output_preview: object | None = Field(default=None, alias="outputPreview")
    handler_called: bool = Field(default=False, alias="handlerCalled")


class Gate1AReadOnlyToolBundle(_Gate1AModel):
    status: Gate1AStatus
    reason: str
    host: "Gate1AReadOnlyToolHost"
    tools: tuple[FunctionTool, ...] = ()
    exposed_tool_names: tuple[str, ...] = Field(default=(), alias="exposedToolNames")
    attachment_flags: Gate1AAttachmentFlags = Field(alias="attachmentFlags")
    source_ledger_projection: dict[str, object] = Field(
        default_factory=dict,
        alias="sourceLedgerProjection",
    )


class Gate1AToolCounter:
    def __init__(self, config: Gate1AReadOnlyToolConfig) -> None:
        self._config = config
        self._records: dict[tuple[str, str], Gate1AToolReceipt] = {}
        self._argument_digests: dict[tuple[str, str], str] = {}
        self._tool_calls = 0
        self._aggregate_bytes = 0

    def before_call(
        self,
        *,
        request_digest: str,
        tool_call_digest: str,
        argument_digest: str,
        tool_name: str,
    ) -> Gate1AToolOutcome | None:
        key = (request_digest, tool_call_digest)
        existing = self._records.get(key)
        if existing is not None:
            if self._argument_digests.get(key) == argument_digest:
                return Gate1AToolOutcome(
                    status="duplicate",
                    reason="duplicate_tool_call",
                    receipt=existing,
                    outputPreview=None,
                    handlerCalled=False,
                )
            return self.blocked(
                request_digest=request_digest,
                tool_call_digest=tool_call_digest,
                tool_name=tool_name,
                reason="tool_call_digest_conflict",
            )
        if self._tool_calls >= self._config.max_tool_calls_per_turn:
            return self.blocked(
                request_digest=request_digest,
                tool_call_digest=tool_call_digest,
                tool_name=tool_name,
                reason="max_tool_calls_exhausted",
            )
        return None

    @property
    def receipt_count(self) -> int:
        return len(self._records)

    @property
    def receipts(self) -> tuple[Gate1AToolReceipt, ...]:
        return tuple(self._records.values())

    def finish_call(
        self,
        *,
        request_digest: str,
        tool_call_digest: str,
        argument_digest: str,
        tool_name: str,
        status: Gate1AOutcomeStatus,
        output_preview: object | None,
        redaction_proof: Literal["redacted", "no_redaction_needed"],
        output_byte_count: int,
    ) -> Gate1AToolReceipt:
        bounded_count = min(output_byte_count, self._config.max_per_tool_output_bytes)
        if self._aggregate_bytes + bounded_count > self._config.max_aggregate_output_bytes:
            receipt = _receipt(
                request_digest=request_digest,
                tool_call_digest=tool_call_digest,
                tool_name=tool_name,
                status="blocked",
                output_preview=None,
                output_byte_count=0,
                redaction_proof="no_redaction_needed",
            )
            self._records[(request_digest, tool_call_digest)] = receipt
            self._argument_digests[(request_digest, tool_call_digest)] = argument_digest
            return receipt
        receipt = _receipt(
            request_digest=request_digest,
            tool_call_digest=tool_call_digest,
            tool_name=tool_name,
            status=status,
            output_preview=output_preview,
            output_byte_count=bounded_count,
            redaction_proof=redaction_proof,
        )
        self._records[(request_digest, tool_call_digest)] = receipt
        self._argument_digests[(request_digest, tool_call_digest)] = argument_digest
        self._tool_calls += 1
        self._aggregate_bytes += bounded_count
        return receipt

    def blocked(
        self,
        *,
        request_digest: str,
        tool_call_digest: str,
        tool_name: str,
        reason: str,
    ) -> Gate1AToolOutcome:
        return Gate1AToolOutcome(
            status="blocked",
            reason=reason,
            receipt=_receipt(
                request_digest=request_digest,
                tool_call_digest=tool_call_digest,
                tool_name=tool_name,
                status="blocked",
                output_preview=None,
                output_byte_count=0,
                redaction_proof="no_redaction_needed",
            ),
            outputPreview=None,
            handlerCalled=False,
        )


class Gate1AReadOnlyToolHost:
    def __init__(
        self,
        *,
        config: Gate1AReadOnlyToolConfig,
        workspace_root: Path,
        exposed_tool_names: tuple[str, ...],
        now_ms: Callable[[], int],
        artifact_provider: Mapping[str, object] | None = None,
        task_provider: Mapping[str, object] | None = None,
        cron_provider: Mapping[str, object] | None = None,
    ) -> None:
        self.config = config
        self.workspace_root = workspace_root.resolve()
        self.exposed_tool_names = exposed_tool_names
        self.now_ms = now_ms
        self.artifact_provider = dict(artifact_provider or {})
        self.task_provider = dict(task_provider or {})
        self.cron_provider = dict(cron_provider or {})
        self.counter = Gate1AToolCounter(config)
        self.call_count = 0

    async def dispatch(
        self,
        tool_name: str,
        arguments: Mapping[str, object] | None = None,
        *,
        request_digest: str,
        tool_call_id: str,
    ) -> Gate1AToolOutcome:
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
            )
        self.call_count += 1
        try:
            raw_output = self._execute(tool_name, args)
            output_preview, redaction_proof, output_byte_count = _bounded_safe_output(
                raw_output,
                max_bytes=self.config.max_per_tool_output_bytes,
            )
            receipt = self.counter.finish_call(
                request_digest=request_digest,
                tool_call_digest=tool_call_digest,
                argument_digest=argument_digest,
                tool_name=tool_name,
                status="ok",
                output_preview=output_preview,
                redaction_proof=redaction_proof,
                output_byte_count=output_byte_count,
            )
            if receipt.status == "blocked":
                return Gate1AToolOutcome(
                    status="blocked",
                    reason="aggregate_output_budget_exhausted",
                    receipt=receipt,
                    outputPreview=None,
                    handlerCalled=True,
                )
            return Gate1AToolOutcome(
                status="ok",
                reason="tool_executed",
                receipt=receipt,
                outputPreview=output_preview,
                handlerCalled=True,
            )
        except Gate1APathPolicyError:
            receipt = self.counter.finish_call(
                request_digest=request_digest,
                tool_call_digest=tool_call_digest,
                argument_digest=argument_digest,
                tool_name=tool_name,
                status="blocked",
                output_preview=None,
                redaction_proof="no_redaction_needed",
                output_byte_count=0,
            )
            return Gate1AToolOutcome(
                status="blocked",
                reason="path_policy_denied",
                receipt=receipt,
                outputPreview=None,
                handlerCalled=True,
            )
        except Exception:
            receipt = self.counter.finish_call(
                request_digest=request_digest,
                tool_call_digest=tool_call_digest,
                argument_digest=argument_digest,
                tool_name=tool_name,
                status="error",
                output_preview=None,
                redaction_proof="no_redaction_needed",
                output_byte_count=0,
            )
            return Gate1AToolOutcome(
                status="error",
                reason="tool_handler_error",
                receipt=receipt,
                outputPreview=None,
                handlerCalled=True,
            )

    def _execute(self, tool_name: str, args: Mapping[str, object]) -> object:
        if tool_name == "Clock":
            return self.now_ms()
        if tool_name == "Calculation":
            return {"value": _evaluate_expression(str(args.get("expression", "0")))}
        if tool_name == "FileRead":
            path = _safe_child_path(self.workspace_root, str(args.get("path", "")))
            return path.read_text(encoding="utf-8", errors="replace")
        if tool_name == "Glob":
            pattern = str(args.get("pattern", "*"))
            return {
                "matches": [
                    str(path.relative_to(self.workspace_root))
                    for path in _safe_glob_files(self.workspace_root, pattern, limit=64)
                ]
            }
        if tool_name == "Grep":
            pattern = re.compile(str(args.get("pattern", "")))
            glob_pattern = str(args.get("glob", "**/*"))
            matches: list[dict[str, object]] = []
            for path in _safe_glob_files(self.workspace_root, glob_pattern, limit=128):
                text = path.read_text(encoding="utf-8", errors="replace")
                for index, line in enumerate(text.splitlines(), start=1):
                    if pattern.search(line):
                        matches.append(
                            {
                                "path": str(path.relative_to(self.workspace_root)),
                                "line": index,
                                "matched": True,
                            }
                        )
                    if len(matches) >= 64:
                        return {"matches": matches}
            return {"matches": matches}
        if tool_name == "GitDiff":
            return {
                "status": "local_metadata_only",
                "workspaceLooksLikeGit": (self.workspace_root / ".git").exists(),
            }
        if tool_name == "ArtifactList":
            return {"artifacts": sorted(self.artifact_provider)}
        if tool_name == "ArtifactRead":
            key = str(args.get("artifactId", ""))
            return {"artifactId": key, "metadata": self.artifact_provider.get(key)}
        if tool_name == "HealthStatus":
            return {"status": "ok", "surface": "gate1a_readonly_tools"}
        if tool_name == "TaskList":
            return {"tasks": sorted(self.task_provider)}
        if tool_name == "TaskGet":
            key = str(args.get("taskId", ""))
            return {"taskId": key, "metadata": self.task_provider.get(key)}
        if tool_name == "TaskOutput":
            key = str(args.get("taskId", ""))
            return {"taskId": key, "outputRef": f"task-output:{_digest(key)[7:23]}"}
        if tool_name == "CronList":
            return {"crons": sorted(self.cron_provider)}
        raise ValueError("unsupported tool")


def build_gate1a_readonly_tool_bundle(
    *,
    config: Gate1AReadOnlyToolConfig | Mapping[str, object] | None = None,
    scope: Mapping[str, object] | None = None,
    workspace_root: str | Path,
    now_ms: Callable[[], int] | None = None,
    artifact_provider: Mapping[str, object] | None = None,
    task_provider: Mapping[str, object] | None = None,
    cron_provider: Mapping[str, object] | None = None,
) -> Gate1AReadOnlyToolBundle:
    safe_config = Gate1AReadOnlyToolConfig.model_validate(config or {})
    selected_scope_error = _selected_scope_error(safe_config, scope or {})
    exposed = (
        _selected_readonly_tool_names(safe_config.allowed_tool_names)
        if selected_scope_error is None
        else ()
    )
    host = Gate1AReadOnlyToolHost(
        config=safe_config,
        workspace_root=Path(workspace_root),
        exposed_tool_names=exposed,
        now_ms=now_ms or _now_ms,
        artifact_provider=artifact_provider,
        task_provider=task_provider,
        cron_provider=cron_provider,
    )
    if selected_scope_error is not None:
        return Gate1AReadOnlyToolBundle(
            status="disabled" if selected_scope_error == "gate_disabled" else "blocked",
            reason=selected_scope_error,
            host=host,
            tools=(),
            exposedToolNames=(),
            attachmentFlags=Gate1AAttachmentFlags(),
            sourceLedgerProjection=_source_ledger_projection(()),
        )
    tools = tuple(_build_adk_tool(host, name) for name in exposed)
    return Gate1AReadOnlyToolBundle(
        status="ready",
        reason="selected_readonly_tools_ready",
        host=host,
        tools=tools,
        exposedToolNames=exposed,
        attachmentFlags=Gate1AAttachmentFlags(
            localReadOnlyToolsAttached=safe_config.local_test_harness_enabled,
            adkFunctionToolsBuilt=bool(tools),
            routeAttached=safe_config.route_attachment_enabled,
        ),
        sourceLedgerProjection=_source_ledger_projection(exposed),
    )


def _selected_scope_error(
    config: Gate1AReadOnlyToolConfig,
    scope: Mapping[str, object],
) -> str | None:
    if not config.enabled:
        return "gate_disabled"
    if config.kill_switch_enabled:
        return "kill_switch_enabled"
    if not config.local_test_harness_enabled and not config.route_attachment_enabled:
        return "local_test_harness_disabled"
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
    return None


def _selected_readonly_tool_names(names: Sequence[str]) -> tuple[str, ...]:
    allowed = set(names)
    return tuple(
        name
        for name in _READONLY_TOOL_ORDER
        if name in allowed and name not in _FORBIDDEN_TOOL_NAMES
    )


def _build_adk_tool(host: Gate1AReadOnlyToolHost, name: str) -> FunctionTool:
    if name == "Clock":

        async def invoke_clock(tool_context: object | None = None) -> dict[str, object]:
            return await _dispatch_adk_tool(host, "Clock", {}, tool_context)

        return _function_tool(name, invoke_clock)

    if name == "Calculation":

        async def invoke_calculation(
            expression: str = "0",
            tool_context: object | None = None,
        ) -> dict[str, object]:
            return await _dispatch_adk_tool(
                host,
                "Calculation",
                {"expression": expression},
                tool_context,
            )

        return _function_tool(name, invoke_calculation)

    if name == "FileRead":

        async def invoke_file_read(
            path: str = "",
            tool_context: object | None = None,
        ) -> dict[str, object]:
            return await _dispatch_adk_tool(host, "FileRead", {"path": path}, tool_context)

        return _function_tool(name, invoke_file_read)

    if name == "Glob":

        async def invoke_glob(
            pattern: str = "*",
            tool_context: object | None = None,
        ) -> dict[str, object]:
            return await _dispatch_adk_tool(host, "Glob", {"pattern": pattern}, tool_context)

        return _function_tool(name, invoke_glob)

    if name == "Grep":

        async def invoke_grep(
            pattern: str = "",
            glob: str = "**/*",
            tool_context: object | None = None,
        ) -> dict[str, object]:
            return await _dispatch_adk_tool(
                host,
                "Grep",
                {"pattern": pattern, "glob": glob},
                tool_context,
            )

        return _function_tool(name, invoke_grep)

    if name == "GitDiff":

        async def invoke_git_diff(tool_context: object | None = None) -> dict[str, object]:
            return await _dispatch_adk_tool(host, "GitDiff", {}, tool_context)

        return _function_tool(name, invoke_git_diff)

    if name == "ArtifactList":

        async def invoke_artifact_list(
            tool_context: object | None = None,
        ) -> dict[str, object]:
            return await _dispatch_adk_tool(host, "ArtifactList", {}, tool_context)

        return _function_tool(name, invoke_artifact_list)

    if name == "ArtifactRead":

        async def invoke_artifact_read(
            artifactId: str = "",
            tool_context: object | None = None,
        ) -> dict[str, object]:
            return await _dispatch_adk_tool(
                host,
                "ArtifactRead",
                {"artifactId": artifactId},
                tool_context,
            )

        return _function_tool(name, invoke_artifact_read)

    if name == "HealthStatus":

        async def invoke_health_status(
            tool_context: object | None = None,
        ) -> dict[str, object]:
            return await _dispatch_adk_tool(host, "HealthStatus", {}, tool_context)

        return _function_tool(name, invoke_health_status)

    if name == "TaskList":

        async def invoke_task_list(tool_context: object | None = None) -> dict[str, object]:
            return await _dispatch_adk_tool(host, "TaskList", {}, tool_context)

        return _function_tool(name, invoke_task_list)

    if name == "TaskGet":

        async def invoke_task_get(
            taskId: str = "",
            tool_context: object | None = None,
        ) -> dict[str, object]:
            return await _dispatch_adk_tool(host, "TaskGet", {"taskId": taskId}, tool_context)

        return _function_tool(name, invoke_task_get)

    if name == "TaskOutput":

        async def invoke_task_output(
            taskId: str = "",
            tool_context: object | None = None,
        ) -> dict[str, object]:
            return await _dispatch_adk_tool(
                host,
                "TaskOutput",
                {"taskId": taskId},
                tool_context,
            )

        return _function_tool(name, invoke_task_output)

    if name == "CronList":

        async def invoke_cron_list(tool_context: object | None = None) -> dict[str, object]:
            return await _dispatch_adk_tool(host, "CronList", {}, tool_context)

        return _function_tool(name, invoke_cron_list)

    raise ValueError("unsupported Gate 1A read-only tool")


async def _dispatch_adk_tool(
    host: Gate1AReadOnlyToolHost,
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
    func.__doc__ = f"Gate 1A read-only {name} tool."
    return FunctionTool(func, require_confirmation=False)


def build_gate1a_registry(exposed_tool_names: Sequence[str]) -> ToolRegistry:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)
    for manifest in _extra_manifests():
        if registry.resolve_registration(manifest.name) is None:
            registry.register(manifest)
    for manifest in registry.list_all():
        if manifest.name in exposed_tool_names:
            registry.enable(manifest.name)
    return registry


def _extra_manifests() -> tuple[ToolManifest, ...]:
    source = ToolSource(kind="native-plugin", package="openmagi.gate1a")
    return tuple(
        ToolManifest(
            name=name,
            description=f"Gate 1A read-only {name} metadata tool.",
            kind="native",
            source=source,
            permission=_permission_for(name),
            inputSchema={"type": "object", "additionalProperties": True},
            timeoutMs=1000,
            budget=Budget(max_calls_per_turn=8, max_parallel=1, outputChars=4096),
            isConcurrencySafe=True,
            parallelSafety="readonly",
            availableInModes=("plan", "act"),
            tags=("gate1a", "read-only"),
            enabled_by_default=False,
            opt_out=True,
        )
        for name in (
            "HealthStatus",
            "TaskList",
            "TaskGet",
            "TaskOutput",
            "CronList",
        )
    )


def _permission_for(name: str) -> PermissionClass:
    return "meta" if name in {"HealthStatus", "TaskList", "TaskGet", "TaskOutput", "CronList"} else "read"


def _safe_child_path(root: Path, path_text: str) -> Path:
    candidate = (root / path_text).resolve()
    if root not in (candidate, *candidate.parents):
        raise Gate1APathPolicyError("path escaped workspace")
    try:
        relative_path = candidate.relative_to(root)
    except ValueError as exc:
        raise Gate1APathPolicyError("path escaped workspace") from exc
    if _is_sensitive_workspace_path(relative_path):
        raise Gate1APathPolicyError("path is protected")
    if not candidate.is_file():
        raise Gate1APathPolicyError("path is not readable file")
    return candidate


def _safe_glob_files(root: Path, pattern: str, *, limit: int) -> tuple[Path, ...]:
    normalized = _normalize_safe_glob_pattern(pattern)
    if normalized is None:
        return ()
    safe_files: list[Path] = []
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
            if not _glob_pattern_matches(relative, normalized):
                continue
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if root not in (resolved, *resolved.parents):
                continue
            if not resolved.is_file():
                continue
            safe_files.append(resolved)
            if len(safe_files) >= limit:
                return tuple(safe_files)
    return tuple(safe_files)


def _is_sensitive_workspace_path(relative_path: Path) -> bool:
    for part in relative_path.parts:
        if not part or part in {".", ".."}:
            return True
        if part.startswith("."):
            return True
        if _SENSITIVE_PATH_PART_RE.search(part):
            return True
    return False


def _normalize_safe_glob_pattern(pattern: str) -> str | None:
    normalized = str(pattern or "*").replace("\\", "/").strip()
    if not normalized:
        return "*"
    if normalized.startswith(("/", "~")):
        return None
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        return None
    return "/".join(parts) or "*"


def _glob_pattern_matches(relative_path: str, pattern: str) -> bool:
    if pattern in {"**", "**/*"}:
        return True
    if pattern.startswith("**/"):
        suffix = pattern[3:]
        return fnmatch.fnmatchcase(relative_path, suffix) or fnmatch.fnmatchcase(
            relative_path,
            pattern,
        )
    if "/" not in pattern and "/" in relative_path:
        return False
    return fnmatch.fnmatchcase(relative_path, pattern)


def _evaluate_expression(expression: str) -> int | float:
    tree = ast.parse(expression, mode="eval")
    return _eval_ast(tree.body)


def _eval_ast(node: ast.AST) -> int | float:
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


def _bounded_safe_output(
    value: object,
    *,
    max_bytes: int,
) -> tuple[object | None, Literal["redacted", "no_redaction_needed"], int]:
    sanitized, redacted = _sanitize_output(value)
    encoded = json.dumps(
        sanitized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=repr,
    ).encode("utf-8")
    if len(encoded) > max_bytes:
        preview: object | None = {"truncated": True, "digest": _digest(sanitized)}
        return preview, "redacted" if redacted else "no_redaction_needed", max_bytes
    return sanitized, "redacted" if redacted else "no_redaction_needed", len(encoded)


def _sanitize_output(value: object) -> tuple[object | None, bool]:
    if isinstance(value, Mapping):
        redacted = False
        safe: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if _SENSITIVE_KEY_RE.search(key_text):
                redacted = True
                continue
            nested, nested_redacted = _sanitize_output(item)
            safe[key_text[:80]] = nested
            redacted = redacted or nested_redacted
        return safe, redacted
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        redacted = False
        safe_items: list[object | None] = []
        for item in value[:64]:
            nested, nested_redacted = _sanitize_output(item)
            safe_items.append(nested)
            redacted = redacted or nested_redacted
        return safe_items, redacted
    if isinstance(value, str):
        if _SENSITIVE_RE.search(value):
            return "[redacted]", True
        if any(fnmatch.fnmatchcase(value.lower(), pattern) for pattern in ("*secret*", "*token*")):
            return "[redacted]", True
        return value[:512], False
    if isinstance(value, bool | int | float) or value is None:
        return value, False
    return repr(value)[:256], False


def _receipt(
    *,
    request_digest: str,
    tool_call_digest: str,
    tool_name: str,
    status: Gate1AOutcomeStatus,
    output_preview: object | None,
    output_byte_count: int,
    redaction_proof: Literal["redacted", "no_redaction_needed"],
) -> Gate1AToolReceipt:
    return Gate1AToolReceipt(
        requestDigest=request_digest,
        toolCallDigest=tool_call_digest,
        allowedToolName=tool_name,
        status=status,
        boundedOutputDigest=_digest(output_preview),
        outputByteCount=output_byte_count,
        redactionProof=redaction_proof,
    )


def _source_ledger_projection(tool_names: Sequence[str]) -> dict[str, object]:
    return {
        "schemaVersion": "gate1a.sourceLedgerProjection.v1",
        "sourceLedgerReadOnly": True,
        "projectionMetadataOnly": True,
        "projectedToolNames": tuple(tool_names),
        "rawSourceRecordsIncluded": False,
    }


def _digest(value: object) -> str:
    if isinstance(value, str) and _DIGEST_RE.fullmatch(value):
        return value
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=repr,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _now_ms() -> int:
    return int(time.time() * 1000)


Gate1AReadOnlyToolBundle.model_rebuild()


__all__ = [
    "GATE1A_FORBIDDEN_TOOL_NAMES",
    "GATE1A_READONLY_TOOL_NAMES",
    "Gate1AAttachmentFlags",
    "Gate1AReadOnlyToolBundle",
    "Gate1AReadOnlyToolConfig",
    "Gate1AReadOnlyToolHost",
    "Gate1AToolCounter",
    "Gate1AToolOutcome",
    "Gate1AToolReceipt",
    "build_gate1a_readonly_tool_bundle",
    "build_gate1a_registry",
]
