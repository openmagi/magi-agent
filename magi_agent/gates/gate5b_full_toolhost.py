from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Self

from google.adk.tools import FunctionTool
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from magi_agent.evidence.coding_tool_receipts import (
    CodingToolReceiptBoundary,
    CodingToolReceiptConfig,
    CodingToolReceiptRecord,
)
from magi_agent.tools.result import ToolResult


Gate5BFullToolHostStatus = Literal["disabled", "blocked", "ready"]
Gate5BFullToolOutcomeStatus = Literal["ok", "blocked", "error", "duplicate"]

GATE5B_FULL_TOOLHOST_TOOL_NAMES = (
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
        )

    def blocked(
        self,
        *,
        request_digest: str,
        tool_call_digest: str,
        tool_name: str,
        reason: str,
    ) -> Gate5BFullToolOutcome:
        receipt = Gate5BFullToolReceipt(
            requestDigest=request_digest,
            toolCallDigest=tool_call_digest,
            toolName=tool_name,
            status="blocked",
            boundedOutputDigest=_digest({"blocked": reason}),
            outputByteCount=0,
        )
        self._records[(request_digest, tool_call_digest)] = receipt
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
    ) -> None:
        self.config = config
        self.workspace_root = workspace_root.resolve()
        self.exposed_tool_names = exposed_tool_names
        self.now_ms = now_ms
        self.counter = Gate5BFullToolCounter(config)
        self.receipt_boundary = CodingToolReceiptBoundary(
            CodingToolReceiptConfig(enabled=True)
        )

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
            )
        try:
            output = self._handle(tool_name, args)
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
            )
        except Gate5BFullToolPathPolicyError:
            return self.counter.blocked(
                request_digest=request_digest,
                tool_call_digest=tool_call_digest,
                tool_name=tool_name,
                reason="path_policy_denied",
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

    def _handle(self, tool_name: str, args: Mapping[str, object]) -> object:
        if tool_name == "Clock":
            return {"nowMs": self.now_ms()}
        if tool_name == "Calculation":
            return {"value": _evaluate_expression(str(args.get("expression", "0")))}
        if tool_name == "FileRead":
            target = _safe_child_path(self.workspace_root, str(args.get("path", "")))
            return {"pathDigest": _digest(target.relative_to(self.workspace_root).as_posix()), "content": target.read_text(encoding="utf-8", errors="replace")[: self.config.max_per_tool_output_bytes]}
        if tool_name == "Glob":
            return {"matches": _safe_glob_files(self.workspace_root, str(args.get("pattern", "*")), limit=100)}
        if tool_name == "Grep":
            pattern = str(args.get("pattern", ""))
            matches: list[dict[str, object]] = []
            if not pattern:
                return {"matches": matches}
            for relative in _safe_glob_files(self.workspace_root, str(args.get("glob", "**/*")), limit=200):
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
            target.parent.mkdir(parents=True, exist_ok=True)
            content = str(args.get("content", ""))
            target.write_text(content, encoding="utf-8")
            return {"pathDigest": _digest(target.relative_to(self.workspace_root).as_posix()), "bytesWritten": len(content.encode("utf-8"))}
        if tool_name == "FileEdit":
            target = _safe_child_path(self.workspace_root, str(args.get("path", "")))
            old_text = str(args.get("oldText", args.get("old_text", "")))
            new_text = str(args.get("newText", args.get("new_text", "")))
            if not old_text:
                raise ValueError("empty_old_text")
            current = target.read_text(encoding="utf-8", errors="replace")
            if old_text not in current:
                raise ValueError("old_text_not_found")
            target.write_text(current.replace(old_text, new_text, 1), encoding="utf-8")
            return {"pathDigest": _digest(target.relative_to(self.workspace_root).as_posix()), "replacements": 1}
        if tool_name == "PatchApply":
            target = _safe_child_path(
                self.workspace_root,
                str(args.get("path", "")),
                allow_missing=True,
            )
            if "content" in args:
                content = str(args.get("content", ""))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                return {"pathDigest": _digest(target.relative_to(self.workspace_root).as_posix()), "patchMode": "content_replace", "bytesWritten": len(content.encode("utf-8"))}
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
        raise ValueError("unsupported_tool")


def build_gate5b_full_toolhost_bundle(
    *,
    config: Gate5BFullToolHostConfig | Mapping[str, object] | None = None,
    scope: Mapping[str, object] | None = None,
    workspace_root: str | Path,
    now_ms: Callable[[], int] | None = None,
) -> Gate5BFullToolBundle:
    safe_config = Gate5BFullToolHostConfig.model_validate(config or {})
    workspace = Path(workspace_root)
    selected_scope_error = _selected_scope_error(safe_config, scope or {}, workspace)
    exposed = (
        _selected_tool_names(safe_config.allowed_tool_names)
        if selected_scope_error is None
        else ()
    )
    host = Gate5BFullToolHost(
        config=safe_config,
        workspace_root=workspace,
        exposed_tool_names=exposed,
        now_ms=now_ms or _now_ms,
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

    raise ValueError("unsupported Gate 5B full tool")


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
    if not allow_missing and not candidate.is_file():
        raise Gate5BFullToolPathPolicyError("path is not readable file")
    parent = candidate.parent.resolve(strict=False)
    if root not in (parent, *parent.parents):
        raise Gate5BFullToolPathPolicyError("path escaped workspace")
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


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


__all__ = [
    "GATE5B_FULL_TOOLHOST_TOOL_NAMES",
    "Gate5BFullToolBundle",
    "Gate5BFullToolHost",
    "Gate5BFullToolHostConfig",
    "Gate5BFullToolOutcome",
    "build_gate5b_full_toolhost_bundle",
]
