from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import posixpath
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .context import ToolContext
from .memory_mode_guard import (
    command_may_write_protected_memory,
    command_mentions_protected_memory,
    is_incognito_memory_mode,
    is_long_term_memory_read_disabled,
    is_long_term_memory_write_disabled,
    is_protected_memory_path,
    memory_write_target_paths,
    protected_memory_error,
)
from .registry import ToolRegistry
from .result import ToolResult


CORE_TOOLHOST_DIRECT_TOOL_NAMES = (
    "Clock",
    "Calculation",
    "FileRead",
    "Glob",
    "Grep",
    "FileWrite",
    "FileEdit",
    "PatchApply",
    "Bash",
    "TestRun",
    "GitDiff",
)


def _env_int(
    env: Mapping[str, str],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


class CoreToolhostHandlerSet:
    """Bind the built-in Gate 5B local tool implementations to a registry."""

    def __init__(
        self,
        *,
        allowed_tool_names: tuple[str, ...] = CORE_TOOLHOST_DIRECT_TOOL_NAMES,
        max_tool_calls_per_turn: int = 64,
        command_timeout_ms: int = 5000,
        max_per_tool_output_bytes: int = 8192,
        read_quality_enabled: bool = False,
        read_ledger_enabled: bool = True,
        apply_patch_enabled: bool = True,
        ripgrep_enabled: bool = False,
    ) -> None:
        self.allowed_tool_names = allowed_tool_names
        self.read_ledger_enabled = read_ledger_enabled
        self._config: dict[str, object] = {
            "enabled": True,
            "killSwitchEnabled": False,
            "routeAttachmentEnabled": True,
            "environment": "local",
            "environmentAllowlist": ("local",),
            "allowedToolNames": allowed_tool_names,
            "maxToolCallsPerTurn": max_tool_calls_per_turn,
            "maxPerToolOutputBytes": max_per_tool_output_bytes,
            "commandTimeoutMs": command_timeout_ms,
            "readQualityEnabled": read_quality_enabled,
            "applyPatchEnabled": apply_patch_enabled,
            "ripgrepEnabled": ripgrep_enabled,
        }
        self._hosts: dict[tuple[str, str], Any] = {}

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "CoreToolhostHandlerSet":
        """Build a handler set honoring the runtime tool-cap env contract.

        ``MAGI_TOOL_COMMAND_TIMEOUT_MS`` / ``MAGI_TOOL_MAX_OUTPUT_BYTES`` /
        ``MAGI_TOOL_MAX_CALLS_PER_TURN`` plus the read-quality and ripgrep
        flags. Previously these envs (set by the eval/full profiles) had no
        consumer on this path, so every CLI run executed with the hardcoded
        5s/8KB defaults.
        """
        from magi_agent.config.env import (  # noqa: PLC0415
            is_read_quality_enabled,
            ripgrep_enabled,
        )

        source: Mapping[str, str] = os.environ if env is None else env
        return cls(
            max_tool_calls_per_turn=_env_int(
                source,
                "MAGI_TOOL_MAX_CALLS_PER_TURN",
                default=64,
                minimum=1,
                maximum=4096,
            ),
            command_timeout_ms=_env_int(
                source,
                "MAGI_TOOL_COMMAND_TIMEOUT_MS",
                default=5000,
                minimum=250,
                maximum=600000,
            ),
            max_per_tool_output_bytes=_env_int(
                source,
                "MAGI_TOOL_MAX_OUTPUT_BYTES",
                default=8192,
                minimum=1,
                maximum=131072,
            ),
            read_quality_enabled=is_read_quality_enabled(source),
            ripgrep_enabled=ripgrep_enabled(source),
        )

    def bind(self, registry: ToolRegistry) -> tuple[str, ...]:
        bound: list[str] = []
        for name in self.allowed_tool_names:
            registration = registry.resolve_registration(name)
            if registration is None or registration.handler is not None:
                continue
            registry.bind_handler(
                name,
                self._handler_for(name),
                enabled_by_registry_policy=True,
            )
            bound.append(name)
        return tuple(bound)

    def _handler_for(self, tool_name: str):
        async def handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
            blocked = _memory_mode_block(tool_name, arguments, context)
            if blocked is not None:
                return blocked
            workspace_root = _workspace_root(context)
            host = self._host_for(workspace_root, context)
            request_digest = _request_digest(context)
            tool_call_id = _tool_call_id(tool_name, arguments, context)
            outcome = await host.dispatch(
                tool_name,
                arguments,
                request_digest=request_digest,
                tool_call_id=tool_call_id,
            )
            result = _tool_result_from_outcome(outcome)
            return _memory_mode_filter_result(tool_name, result, context)

        return handler

    def _host_for(self, workspace_root: Path, context: ToolContext) -> Any:
        key = (str(workspace_root), context.turn_id or context.session_id or "local-turn")
        host = self._hosts.get(key)
        if host is None:
            from magi_agent.gates.gate5b_full_toolhost import (
                Gate5BFullToolHost,
                Gate5BFullToolHostConfig,
            )

            host = Gate5BFullToolHost(
                config=Gate5BFullToolHostConfig.model_validate(self._config),
                workspace_root=workspace_root,
                exposed_tool_names=self.allowed_tool_names,
                now_ms=lambda: 0,
                read_ledger_enabled=self.read_ledger_enabled,
            )
            self._hosts[key] = host
        return host


def bind_core_toolhost_handlers(registry: ToolRegistry) -> tuple[str, ...]:
    """Attach local first-party core tool handlers to ``registry``.

    The plain catalog remains metadata-only. Runtime builders call this helper
    when they want the default local Magi Agent toolhost to execute core tools.
    """

    return CoreToolhostHandlerSet.from_env().bind(registry)


_MEMORY_WRITE_TOOL_NAMES = frozenset({"FileWrite", "FileEdit", "PatchApply"})
_MEMORY_READ_TOOL_NAMES = frozenset({"FileRead", "Glob", "Grep"})
_PROTECTED_GLOB_SENTINELS = (
    "MEMORY.md",
    "SCRATCHPAD.md",
    "WORKING.md",
    "TASK-QUEUE.md",
    "memory/example.md",
)


def _memory_mode_block(
    tool_name: str,
    arguments: dict[str, object],
    context: ToolContext,
) -> ToolResult | None:
    """Return a blocked ToolResult when the channel memory mode forbids the call.

    Route-independent: any ToolContext carrying a non-normal memory mode is
    enforced here, BEFORE the call reaches the underlying toolhost.
    """

    mode = context.memory_mode
    if tool_name in _MEMORY_READ_TOOL_NAMES:
        if not is_long_term_memory_read_disabled(mode):
            return None
        for path in _memory_mode_read_target_paths(tool_name, arguments):
            if is_protected_memory_path(path):
                return _memory_mode_blocked_result(tool_name, path)
        if tool_name == "Grep" and _grep_glob_may_include_protected_memory(arguments):
            return _memory_mode_blocked_result(tool_name, "memory state")
        return None
    if tool_name in _MEMORY_WRITE_TOOL_NAMES:
        if not is_long_term_memory_write_disabled(mode):
            return None
        for path in memory_write_target_paths(tool_name, arguments):
            if is_protected_memory_path(path):
                return _memory_mode_blocked_result(tool_name, path)
        return None
    if tool_name == "Bash":
        command = arguments.get("command")
        command_text = command if isinstance(command, str) else ""
        blocked = (
            is_incognito_memory_mode(mode)
            and command_mentions_protected_memory(command_text)
        ) or (
            is_long_term_memory_write_disabled(mode)
            and command_may_write_protected_memory(command_text)
        )
        if blocked:
            return _memory_mode_blocked_result(tool_name, "memory state")
        return None
    return None


def _memory_mode_read_target_paths(
    tool_name: str,
    arguments: dict[str, object],
) -> tuple[str, ...]:
    if tool_name == "FileRead":
        names = ("path", "file", "filePath")
    elif tool_name == "Glob":
        names = ("pattern", "glob")
    elif tool_name == "Grep":
        names = ("glob", "path", "patternGlob")
    else:
        names = ()
    paths: list[str] = []
    for name in names:
        value = arguments.get(name)
        if isinstance(value, str) and value:
            paths.append(value)
    return tuple(paths)


def _grep_glob_may_include_protected_memory(arguments: dict[str, object]) -> bool:
    raw_glob = (
        arguments.get("glob")
        or arguments.get("path")
        or arguments.get("patternGlob")
        or "**/*"
    )
    if not isinstance(raw_glob, str):
        return True
    pattern = _normalize_memory_glob(raw_glob)
    if pattern is None:
        return False
    return any(_glob_pattern_matches(path, pattern) for path in _PROTECTED_GLOB_SENTINELS)


def _normalize_memory_glob(pattern: str) -> str | None:
    text = str(pattern or "*").strip().replace("\\", "/")
    if not text:
        return "*"
    if text.startswith(("/", "~")):
        return None
    parts = [part for part in text.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        return None
    normalized = posixpath.normpath("/".join(parts) or "*")
    return "*" if normalized == "." else normalized


def _glob_pattern_matches(relative: str, pattern: str) -> bool:
    if pattern in {"**", "**/*"}:
        return True
    if pattern.startswith("**/"):
        suffix = pattern[3:]
        return fnmatch.fnmatchcase(relative, suffix) or fnmatch.fnmatchcase(relative, pattern)
    if "/" not in pattern and "/" in relative:
        return False
    return fnmatch.fnmatchcase(relative, pattern)


def _memory_mode_filter_result(
    tool_name: str,
    result: ToolResult,
    context: ToolContext,
) -> ToolResult:
    if (
        tool_name not in {"Glob", "Grep"}
        or not is_long_term_memory_read_disabled(context.memory_mode)
        or result.status != "ok"
        or not isinstance(result.output, Mapping)
    ):
        return result
    matches = result.output.get("matches")
    if not isinstance(matches, list):
        return result
    filtered = [
        match for match in matches if not is_protected_memory_path(_match_path(match))
    ]
    if len(filtered) == len(matches):
        return result
    output = dict(result.output)
    output["matches"] = filtered
    return result.model_copy(update={"output": output})


def _match_path(match: object) -> str | None:
    if isinstance(match, str):
        return match
    if isinstance(match, Mapping):
        path = match.get("path")
        return path if isinstance(path, str) else None
    return None


def _memory_mode_blocked_result(tool_name: str, path_label: str) -> ToolResult:
    return ToolResult(
        status="blocked",
        errorCode="memory_mode_blocked",
        errorMessage=protected_memory_error(path_label),
        metadata={
            "toolName": tool_name,
            "reason": "memory_mode_blocked",
        },
    )


def _workspace_root(context: ToolContext) -> Path:
    raw = context.workspace_root or os.getcwd()
    root = Path(raw).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _request_digest(context: ToolContext) -> str:
    return _digest(
        {
            "botId": context.bot_id,
            "sessionId": context.session_id,
            "turnId": context.turn_id,
            "traceId": context.trace_id,
        }
    )


def _tool_call_id(
    tool_name: str,
    arguments: dict[str, object],
    context: ToolContext,
) -> str:
    if context.tool_use_id:
        return context.tool_use_id
    return f"core-toolhost:{tool_name}:{_digest(arguments)[7:23]}"


def _tool_result_from_outcome(outcome) -> ToolResult:
    receipt = outcome.receipt.model_dump(by_alias=True, mode="json", warnings=False)
    metadata: dict[str, object] = {
        "toolName": outcome.receipt.tool_name,
        "reason": outcome.reason,
        "gate5bFullToolhostReceipt": receipt,
    }
    if outcome.coding_mutation_receipt is not None:
        metadata["codingMutationReceipt"] = outcome.coding_mutation_receipt.public_projection()
    if outcome.code_diagnostics_receipt is not None:
        metadata["codeDiagnosticsReceipt"] = outcome.code_diagnostics_receipt.public_projection()
    if outcome.status == "ok":
        return ToolResult(
            status="ok",
            output=outcome.output_preview,
            metadata=metadata,
            codingMutationReceipt=outcome.coding_mutation_receipt,
        )
    if outcome.status == "duplicate":
        metadata["duplicateToolCall"] = True
        return ToolResult(status="ok", output=outcome.output_preview, metadata=metadata)
    if outcome.status == "blocked":
        return ToolResult(
            status="blocked",
            errorCode=outcome.reason,
            errorMessage=outcome.reason,
            metadata=metadata,
        )
    return ToolResult(
        status="error",
        errorCode=outcome.reason,
        errorMessage=outcome.reason,
        output=outcome.output_preview,
        metadata=metadata,
        codingMutationReceipt=outcome.coding_mutation_receipt,
    )


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "CORE_TOOLHOST_DIRECT_TOOL_NAMES",
    "CoreToolhostHandlerSet",
    "bind_core_toolhost_handlers",
]
