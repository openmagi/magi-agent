from __future__ import annotations

import os
import posixpath
import re
import shlex
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from .context import ToolContext
from .manifest import RuntimeMode, ToolManifest
from .read_ledger import ReadLedger, WorkspaceMutationReadCheck, WorkspaceMutationReadDecision


SafetyAction = Literal["allow", "deny", "ask"]

_SYSTEM_ABSOLUTE_PREFIXES = (
    "/bin/",
    "/boot/",
    "/dev/",
    "/etc/",
    "/lib/",
    "/lib64/",
    "/proc/",
    "/root/",
    "/sbin/",
    "/sys/",
    "/usr/",
    "/var/",
    "/System/",
    "/Library/",
)
_SYSTEM_ABSOLUTE_PATHS = {
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/proc",
    "/root",
    "/sys",
    "/usr",
    "/var",
}
_SEALED_BASENAMES = {"AGENTS.md", "CLAUDE.md", "SOUL.md", "TOOLS.md", "HEARTBEAT.md"}
_SECRET_NAME_RE = re.compile(
    r"(^|/)(?:\.env(?:[./_-]|$)|.*(?:^|[._/-])"
    r"(?:secrets?|tokens?|credentials?|sessions?|api[_-]?keys?|"
    r"private(?:[_-]?keys?)?|passwords?|kubeconfig)"
    r"(?:[._/-]|$).*)",
    re.IGNORECASE,
)
_SECRET_BASENAMES = {".netrc", ".npmrc", ".pypirc", "id_rsa", "kubeconfig"}
_SECRET_SUFFIXES = ("/.aws/credentials", "/.kube/config")
_MUTATING_TOOLS = {"FileWrite", "FileEdit", "PatchApply"}
_DELEGATION_TOOL_NAMES = {"Delegate", "Task", "TaskGet", "TaskWait"}
_PATH_ARG_NAMES = ("path", "file", "target", "targetPath", "filePath")
_SAFE_COMMAND_EXECUTABLES = {
    "cat",
    "find",
    "git",
    "grep",
    "head",
    "jq",
    "ls",
    "nl",
    "pwd",
    "rg",
    "sed",
    "tail",
    "test",
    "wc",
}
_SHELL_EXECUTABLES = {"bash", "fish", "sh", "zsh"}
_READONLY_SHELL_COMMANDS = {
    "cat",
    "find",
    "grep",
    "head",
    "jq",
    "ls",
    "nl",
    "pwd",
    "rg",
    "sed",
    "tail",
    "wc",
}
_READONLY_GIT_SUBCOMMANDS = {"diff", "log", "show", "status"}
_UNSAFE_GIT_SUBCOMMANDS = {
    "add",
    "apply",
    "checkout",
    "cherry-pick",
    "clean",
    "commit",
    "merge",
    "mv",
    "pull",
    "push",
    "rebase",
    "reset",
    "restore",
    "rm",
    "switch",
    "tag",
}
_NETWORK_EXECUTABLES = {"curl", "ftp", "nc", "rsync", "scp", "sftp", "ssh", "wget"}
_SHELL_INLINE_CODE_FLAGS = {"-c"}
_INLINE_CODE_FLAGS_BY_EXECUTABLE = {
    "bash": _SHELL_INLINE_CODE_FLAGS,
    "bun": {"-e"},
    "fish": _SHELL_INLINE_CODE_FLAGS,
    "node": {"-e", "--eval", "-p", "--print"},
    "osascript": {"-e"},
    "perl": {"-e"},
    "php": {"-r", "-B", "-R", "-E"},
    "ruby": {"-e"},
    "sh": _SHELL_INLINE_CODE_FLAGS,
    "zsh": _SHELL_INLINE_CODE_FLAGS,
}
_NETWORK_UPLOAD_FLAGS = {
    "--data",
    "--data-ascii",
    "--data-binary",
    "--data-raw",
    "--form",
    "--form-string",
    "--post-file",
    "--upload-file",
    "-d",
    "-F",
    "-T",
}
_MUTATING_FLAGS_BY_EXECUTABLE = {
    "find": {"-delete", "-exec", "-execdir", "-fls", "-fprint", "-fprintf", "-ok", "-okdir"},
    "sed": {"--in-place"},
}
_READONLY_FLAGS_BY_EXECUTABLE = {
    "cat": {
        "--number",
        "--number-nonblank",
        "--show-all",
        "--show-ends",
        "--show-tabs",
        "--squeeze-blank",
        "-A",
        "-E",
        "-T",
        "-b",
        "-e",
        "-n",
        "-s",
        "-t",
        "-v",
    },
    "find": {
        "-depth",
        "-empty",
        "-false",
        "-follow",
        "-group",
        "-iname",
        "-ipath",
        "-iregex",
        "-links",
        "-maxdepth",
        "-mindepth",
        "-name",
        "-newer",
        "-nogroup",
        "-nouser",
        "-path",
        "-perm",
        "-print",
        "-print0",
        "-prune",
        "-regex",
        "-size",
        "-true",
        "-type",
        "-user",
        "-xdev",
    },
    "grep": {
        "--after-context",
        "--before-context",
        "--context",
        "--extended-regexp",
        "--fixed-strings",
        "--ignore-case",
        "--line-number",
        "--recursive",
        "--with-filename",
        "-A",
        "-B",
        "-C",
        "-E",
        "-F",
        "-H",
        "-I",
        "-R",
        "-i",
        "-n",
        "-r",
        "-v",
    },
    "head": {"--bytes", "--lines", "-c", "-n", "-q", "-v"},
    "jq": {"--compact-output", "--raw-output", "--slurp", "-c", "-r", "-s"},
    "ls": {
        "--all",
        "--almost-all",
        "--classify",
        "--directory",
        "--human-readable",
        "--long",
        "-1",
        "-A",
        "-F",
        "-R",
        "-a",
        "-d",
        "-h",
        "-l",
        "-r",
        "-t",
    },
    "nl": {
        "--body-numbering",
        "--number-format",
        "--number-separator",
        "--starting-line-number",
        "--width",
        "-b",
        "-n",
        "-s",
        "-v",
        "-w",
    },
    "rg": {
        "--files",
        "--fixed-strings",
        "--glob",
        "--hidden",
        "--ignore-case",
        "--line-number",
        "--no-heading",
        "--type",
        "-F",
        "-H",
        "-i",
        "-n",
        "-t",
        "-u",
    },
    "sed": {
        "--expression",
        "--file",
        "--quiet",
        "--regexp-extended",
        "--silent",
        "-E",
        "-e",
        "-f",
        "-n",
        "-r",
    },
    "tail": {"--bytes", "--follow", "--lines", "-c", "-f", "-n", "-q", "-v"},
    "test": set(),
    "wc": {"--bytes", "--chars", "--lines", "--words", "-c", "-l", "-m", "-w"},
}
_SHELL_COMPLEX_TOKENS = ("|", ">", "<", ";", "&&", "||", "`", "$(")
_SHELL_BACKGROUND_OPERATOR_RE = re.compile(r"(?<!&)&(?!&)")
_SHELL_VARIABLE_EXPANSION_RE = re.compile(
    r"(?<!\\)\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)"
)


@dataclass(frozen=True)
class RuntimeSafetyDecision:
    action: SafetyAction
    reason: str
    metadata: dict[str, object]


@dataclass(frozen=True)
class _PathDecision:
    action: SafetyAction
    reason_code: str
    normalized: str
    public_preview: str
    path_policy_recorded: bool = False


class RuntimePermissionArbiter:
    def decide(
        self,
        manifest: ToolManifest,
        arguments: dict[str, object],
        context: ToolContext,
        *,
        mode: RuntimeMode,
    ) -> RuntimeSafetyDecision:
        scope = _resolve_scope(context, runtime_mode=mode)

        if manifest.name in {"Bash", "TestRun"}:
            return _shell_decision(manifest, arguments, mode=mode, scope=scope)
        if manifest.name == "SafeCommand":
            return _safe_command_decision(manifest, arguments, mode=mode, scope=scope)
        if manifest.name == "PatchApply":
            return _patch_apply_decision(manifest, arguments, mode=mode, scope=scope)
        if manifest.name == "FileEdit":
            return _file_edit_decision(manifest, arguments, mode=mode, scope=scope)
        if manifest.name in {"FileRead", "FileWrite"}:
            return _file_path_decision(manifest, arguments, mode=mode, scope=scope)
        if scope["source"] == "child_agent" and manifest.name in _DELEGATION_TOOL_NAMES:
            return _child_agent_decision(manifest, arguments, mode=mode, scope=scope)

        if mode == "plan" and manifest.mutates_workspace:
            return _decision(
                "deny",
                manifest,
                mode=mode,
                reason_code="plan_mode_mutation_blocked",
                scope=scope,
            )

        return _decision(
            "allow",
            manifest,
            mode=mode,
            reason_code="not_applicable",
            scope=scope,
            policy_handled=False,
        )


def _child_agent_decision(
    manifest: ToolManifest,
    arguments: dict[str, object],
    *,
    mode: RuntimeMode,
    scope: dict[str, object],
) -> RuntimeSafetyDecision:
    dangerous_argument = arguments.get("dangerous") is True
    if manifest.dangerous or dangerous_argument:
        return _decision(
            "ask",
            manifest,
            mode=mode,
            reason_code="child_agent_dangerous_requires_approval",
            scope=scope,
        )
    return _decision(
        "allow",
        manifest,
        mode=mode,
        reason_code="child_agent_nondangerous_allow",
        scope=scope,
    )


def _file_path_decision(
    manifest: ToolManifest,
    arguments: dict[str, object],
    *,
    mode: RuntimeMode,
    scope: dict[str, object],
) -> RuntimeSafetyDecision:
    path = _first_string(arguments, _PATH_ARG_NAMES)
    if path is None:
        if manifest.name == "FileWrite":
            return _decision(
                "deny",
                manifest,
                mode=mode,
                reason_code="path_required",
                scope=scope,
                preflight=_preflight(False, "path_required"),
            )
        return _decision(
            "allow",
            manifest,
            mode=mode,
            reason_code="not_applicable",
            scope=scope,
            policy_handled=False,
        )
    path_decision = _classify_path(
        path,
        mutating=manifest.name != "FileRead" or manifest.mutates_workspace,
        scope=scope,
    )
    if path_decision.action == "deny":
        return _decision_for_path(path_decision, manifest, mode=mode, scope=scope)
    if mode == "plan" and manifest.name in _MUTATING_TOOLS:
        return _decision_for_path(
            _PathDecision(
                "deny",
                "plan_mode_mutation_blocked",
                path_decision.normalized,
                path_decision.public_preview,
            ),
            manifest,
            mode=mode,
            scope=scope,
        )
    if manifest.name == "FileWrite":
        read_ledger_preflight = _read_ledger_preflight(
            context_read_ledger=scope.get("readLedger"),
            context_session_id=scope.get("sessionId"),
            context_workspace_ref=scope.get("workspaceRef"),
            path=path_decision.normalized,
            current_digest=_current_digest_for_path(
                arguments,
                path_decision.normalized,
                single_path=True,
            ),
            mutation_kind=_file_write_mutation_kind(arguments),
        )
        if read_ledger_preflight is not None and read_ledger_preflight.get("status") != "ok":
            return _read_ledger_block_decision(
                read_ledger_preflight,
                path_decision,
                manifest,
                mode=mode,
                scope=scope,
                changed_files=(path_decision.normalized,),
            )
        if _selected_full_toolhost_scope(scope):
            return _decision_for_path(
                _PathDecision(
                    "allow",
                    "selected_full_toolhost_workspace_mutation_preapproved",
                    path_decision.normalized,
                    path_decision.public_preview,
                    path_decision.path_policy_recorded,
                ),
                manifest,
                mode=mode,
                scope=scope,
                preflight=_preflight(
                    True,
                    None,
                    changed_files=(path_decision.normalized,),
                    read_ledger=read_ledger_preflight,
                ),
                status_metadata=_selected_full_toolhost_status_metadata(),
            )
        return _decision_for_path(
            _PathDecision(
                "ask",
                "workspace_mutation_requires_approval",
                path_decision.normalized,
                path_decision.public_preview,
                path_decision.path_policy_recorded,
            ),
            manifest,
            mode=mode,
            scope=scope,
            preflight=_preflight(
                True,
                None,
                changed_files=(path_decision.normalized,),
                read_ledger=read_ledger_preflight,
            ),
        )
    return _decision_for_path(path_decision, manifest, mode=mode, scope=scope)


def _file_edit_decision(
    manifest: ToolManifest,
    arguments: dict[str, object],
    *,
    mode: RuntimeMode,
    scope: dict[str, object],
) -> RuntimeSafetyDecision:
    path = _first_string(arguments, _PATH_ARG_NAMES)
    if path is None:
        return _decision(
            "deny",
            manifest,
            mode=mode,
            reason_code="path_required",
            scope=scope,
            preflight=_preflight(False, "path_required"),
        )
    path_decision = _classify_path(path, mutating=True, scope=scope)
    if path_decision.action == "deny":
        return _decision_for_path(
            path_decision,
            manifest,
            mode=mode,
            scope=scope,
            preflight=_preflight(False, path_decision.reason_code),
        )
    read_ledger_preflight = _read_ledger_preflight(
        context_read_ledger=scope.get("readLedger"),
        context_session_id=scope.get("sessionId"),
        context_workspace_ref=scope.get("workspaceRef"),
        path=path_decision.normalized,
        current_digest=_first_string(arguments, ("currentDigest", "current_digest", "digest")),
        mutation_kind="edit",
    )
    if read_ledger_preflight is not None and read_ledger_preflight.get("status") != "ok":
        return _read_ledger_block_decision(
            read_ledger_preflight,
            path_decision,
            manifest,
            mode=mode,
            scope=scope,
            changed_files=(path_decision.normalized,),
            dry_run=_truthy(arguments.get("dryRun")),
        )
    if _truthy(arguments.get("dryRun")):
        return _decision_for_path(
            _PathDecision(
                "allow",
                "file_edit_preflight_ok",
                path_decision.normalized,
                path_decision.public_preview,
            ),
            manifest,
            mode=mode,
            scope=scope,
            preflight=_preflight(
                True,
                None,
                dry_run=True,
                changed_files=(path_decision.normalized,),
                read_ledger=read_ledger_preflight,
            ),
        )
    if mode == "plan":
        return _decision_for_path(
            _PathDecision(
                "deny",
                "plan_mode_mutation_blocked",
                path_decision.normalized,
                path_decision.public_preview,
            ),
            manifest,
            mode=mode,
            scope=scope,
            preflight=_preflight(False, "plan_mode_mutation_blocked"),
        )
    if _selected_full_toolhost_scope(scope):
        return _decision_for_path(
            _PathDecision(
                "allow",
                "selected_full_toolhost_workspace_mutation_preapproved",
                path_decision.normalized,
                path_decision.public_preview,
            ),
            manifest,
            mode=mode,
            scope=scope,
            preflight=_preflight(
                True,
                None,
                changed_files=(path_decision.normalized,),
                read_ledger=read_ledger_preflight,
            ),
            status_metadata=_selected_full_toolhost_status_metadata(),
        )
    return _decision_for_path(
        _PathDecision(
            "ask",
            "workspace_mutation_requires_approval",
            path_decision.normalized,
            path_decision.public_preview,
        ),
        manifest,
        mode=mode,
        scope=scope,
        preflight=_preflight(
            True,
            None,
            changed_files=(path_decision.normalized,),
            read_ledger=read_ledger_preflight,
        ),
    )


def _patch_apply_decision(
    manifest: ToolManifest,
    arguments: dict[str, object],
    *,
    mode: RuntimeMode,
    scope: dict[str, object],
) -> RuntimeSafetyDecision:
    patch = _first_string(arguments, ("patch", "diff"))
    if patch is None:
        if isinstance(arguments.get("content"), str):
            return _patch_apply_content_replace_decision(
                manifest,
                arguments,
                mode=mode,
                scope=scope,
            )
        return _decision(
            "deny",
            manifest,
            mode=mode,
            reason_code="patch_required",
            scope=scope,
            preflight=_preflight(False, "patch_required"),
        )
    paths = _extract_patch_paths(patch)
    if not paths:
        return _decision(
            "deny",
            manifest,
            mode=mode,
            reason_code="patch_path_required",
            scope=scope,
            preflight=_preflight(False, "patch_path_required"),
        )
    for path in paths:
        path_decision = _classify_path(path, mutating=True, scope=scope, patch_path=True)
        if path_decision.action == "deny":
            reason_code = path_decision.reason_code
            if reason_code in {
                "path_escapes_workspace",
                "absolute_path_denied",
                "system_path_denied",
            }:
                reason_code = "patch_path_traversal"
            return _decision_for_path(
                _PathDecision(
                    "deny",
                    reason_code,
                    path_decision.normalized,
                    path_decision.public_preview,
                ),
                manifest,
                mode=mode,
                scope=scope,
                preflight=_preflight(False, reason_code),
            )
    changed_files = tuple(dict.fromkeys(_normalize_relative(path) for path in paths))
    read_ledger_preflight = _read_ledger_preflight_for_paths(
        context_read_ledger=scope.get("readLedger"),
        context_session_id=scope.get("sessionId"),
        context_workspace_ref=scope.get("workspaceRef"),
        paths=changed_files,
        arguments=arguments,
        mutation_kind="patch",
    )
    if read_ledger_preflight is not None and read_ledger_preflight.get("status") != "ok":
        reason_code = _read_ledger_reason_code(read_ledger_preflight)
        return _decision(
            "deny",
            manifest,
            mode=mode,
            reason_code=reason_code,
            scope=scope,
            public_preview=f"patch path={changed_files[0]}",
            preflight=_preflight(
                False,
                reason_code,
                dry_run=_truthy(arguments.get("dryRun")),
                changed_files=changed_files,
                hunks=patch.count("@@") or 1,
                read_ledger=read_ledger_preflight,
            ),
        )
    if _truthy(arguments.get("dryRun")):
        return _decision(
            "allow",
            manifest,
            mode=mode,
            reason_code="patch_dry_run_preflight_ok",
            scope=scope,
            public_preview=f"patch path={changed_files[0]}",
            preflight=_preflight(
                True,
                None,
                dry_run=True,
                changed_files=changed_files,
                hunks=patch.count("@@") or 1,
                read_ledger=read_ledger_preflight,
            ),
        )
    if _selected_full_toolhost_scope(scope):
        return _decision(
            "allow",
            manifest,
            mode=mode,
            reason_code="selected_full_toolhost_workspace_mutation_preapproved",
            scope=scope,
            public_preview=f"patch path={changed_files[0]}",
            preflight=_preflight(
                True,
                None,
                changed_files=changed_files,
                hunks=patch.count("@@") or 1,
                read_ledger=read_ledger_preflight,
            ),
            status_metadata=_selected_full_toolhost_status_metadata(),
        )
    return _decision(
        "ask",
        manifest,
        mode=mode,
        reason_code="patch_workspace_mutation_requires_approval",
        scope=scope,
        public_preview=f"patch path={changed_files[0]}",
        preflight=_preflight(
            True,
            None,
            changed_files=changed_files,
            hunks=patch.count("@@") or 1,
            read_ledger=read_ledger_preflight,
        ),
    )


def _patch_apply_content_replace_decision(
    manifest: ToolManifest,
    arguments: dict[str, object],
    *,
    mode: RuntimeMode,
    scope: dict[str, object],
) -> RuntimeSafetyDecision:
    path = _first_string(arguments, _PATH_ARG_NAMES)
    if path is None:
        return _decision(
            "deny",
            manifest,
            mode=mode,
            reason_code="path_required",
            scope=scope,
            preflight=_preflight(False, "path_required"),
        )
    path_decision = _classify_path(path, mutating=True, scope=scope)
    if path_decision.action == "deny":
        return _decision_for_path(
            path_decision,
            manifest,
            mode=mode,
            scope=scope,
            preflight=_preflight(False, path_decision.reason_code),
        )
    read_ledger_preflight = _read_ledger_preflight(
        context_read_ledger=scope.get("readLedger"),
        context_session_id=scope.get("sessionId"),
        context_workspace_ref=scope.get("workspaceRef"),
        path=path_decision.normalized,
        current_digest=_current_digest_for_path(
            arguments,
            path_decision.normalized,
            single_path=True,
        ),
        mutation_kind=_file_write_mutation_kind(arguments),
    )
    if read_ledger_preflight is not None and read_ledger_preflight.get("status") != "ok":
        return _read_ledger_block_decision(
            read_ledger_preflight,
            path_decision,
            manifest,
            mode=mode,
            scope=scope,
            changed_files=(path_decision.normalized,),
        )
    if mode == "plan":
        return _decision_for_path(
            _PathDecision(
                "deny",
                "plan_mode_mutation_blocked",
                path_decision.normalized,
                path_decision.public_preview,
            ),
            manifest,
            mode=mode,
            scope=scope,
            preflight=_preflight(False, "plan_mode_mutation_blocked"),
        )
    if _selected_full_toolhost_scope(scope):
        return _decision_for_path(
            _PathDecision(
                "allow",
                "selected_full_toolhost_workspace_mutation_preapproved",
                path_decision.normalized,
                path_decision.public_preview,
            ),
            manifest,
            mode=mode,
            scope=scope,
            preflight=_preflight(
                True,
                None,
                changed_files=(path_decision.normalized,),
                read_ledger=read_ledger_preflight,
            ),
            status_metadata=_selected_full_toolhost_status_metadata(),
        )
    return _decision_for_path(
        _PathDecision(
            "ask",
            "workspace_mutation_requires_approval",
            path_decision.normalized,
            path_decision.public_preview,
        ),
        manifest,
        mode=mode,
        scope=scope,
        preflight=_preflight(
            True,
            None,
            changed_files=(path_decision.normalized,),
            read_ledger=read_ledger_preflight,
        ),
    )


def _shell_decision(
    manifest: ToolManifest,
    arguments: dict[str, object],
    *,
    mode: RuntimeMode,
    scope: dict[str, object],
) -> RuntimeSafetyDecision:
    command = _first_string(arguments, ("command", "cmd", "script")) or ""
    lowered = command.lower()
    if _is_destructive_shell(lowered):
        reason = "system_shell_denied" if _touches_system_boundary(lowered) else "destructive_shell"
        if _scope_mode(scope) in {"bypass", "selected_full_toolhost"}:
            reason = "bypass_denied_hard_safety"
        return _decision(
            "deny",
            manifest,
            mode=mode,
            reason_code=reason,
            scope=scope,
            public_preview=_preview_command(command),
            status_metadata=(
                _bypass_status_metadata()
                if reason == "bypass_denied_hard_safety"
                else None
            ),
        )
    if _is_curl_pipe_exec(lowered):
        return _decision(
            "deny",
            manifest,
            mode=mode,
            reason_code="curl_pipe_exec",
            scope=scope,
            public_preview=_preview_command(command),
        )
    if _has_network_exfiltration_command(command):
        return _decision(
            "deny",
            manifest,
            mode=mode,
            reason_code="network_exfiltration_denied",
            scope=scope,
            public_preview=_preview_command(command),
        )
    if _has_inline_interpreter_code(command):
        return _decision(
            "deny",
            manifest,
            mode=mode,
            reason_code="interpreter_inline_code_denied",
            scope=scope,
            public_preview=_preview_command(command),
        )
    if (
        _selected_full_toolhost_scope(scope)
        and _has_complex_shell_operator(command)
        and not (
            _trusted_local_shell_enabled()
            and _complex_command_is_read_safe(command)
            and _complex_command_paths_allowed(command, scope=scope)
        )
    ):
        return _decision(
            "deny",
            manifest,
            mode=mode,
            reason_code="complex_shell_requires_approval",
            scope=scope,
            public_preview=_preview_command(command),
        )
    if _is_unsafe_git_shell(command):
        return _decision(
            "deny",
            manifest,
            mode=mode,
            reason_code="unsafe_git",
            scope=scope,
            public_preview=_preview_command(command),
        )
    if _has_shell_path_expansion_command(command):
        return _decision(
            "deny",
            manifest,
            mode=mode,
            reason_code="shell_path_expansion_denied",
            scope=scope,
            public_preview=_preview_command(command),
        )
    readonly_denial = _readonly_shell_denial(command, scope=scope)
    if isinstance(readonly_denial, _PathDecision):
        return _decision_for_path(readonly_denial, manifest, mode=mode, scope=scope)
    if isinstance(readonly_denial, str):
        return _decision(
            "deny",
            manifest,
            mode=mode,
            reason_code=readonly_denial,
            scope=scope,
            public_preview=_preview_command(command),
        )
    if _has_network_command(command):
        return _decision(
            "ask",
            manifest,
            mode=mode,
            reason_code="network_command_requires_approval",
            scope=scope,
            public_preview=_preview_command(command),
        )
    if _selected_full_toolhost_scope(scope):
        return _decision(
            "allow",
            manifest,
            mode=mode,
            reason_code="selected_full_toolhost_shell_preapproved",
            scope=scope,
            public_preview=_preview_command(command),
            status_metadata=_selected_full_toolhost_status_metadata(),
        )
    if _is_simple_readonly_shell(command):
        reason = (
            "bypass_safe_after_security_precheck"
            if _scope_mode(scope) == "bypass"
            else "safe_command_readonly"
        )
        return _decision(
            "allow",
            manifest,
            mode=mode,
            reason_code=reason,
            scope=scope,
            public_preview=_preview_command(command),
        )
    return _decision(
        "ask",
        manifest,
        mode=mode,
        reason_code="complex_shell_requires_approval",
        scope=scope,
        public_preview=_preview_command(command),
    )


def _safe_command_decision(
    manifest: ToolManifest,
    arguments: dict[str, object],
    *,
    mode: RuntimeMode,
    scope: dict[str, object],
) -> RuntimeSafetyDecision:
    executable = _first_string(arguments, ("executable", "command"))
    raw_args = arguments.get("args", ())
    args = tuple(str(arg) for arg in raw_args) if isinstance(raw_args, list | tuple) else ()
    if executable is None:
        return _decision(
            "deny",
            manifest,
            mode=mode,
            reason_code="safe_command_executable_denied",
            scope=scope,
        )
    exe = executable.rsplit("/", 1)[-1]
    if exe in _SHELL_EXECUTABLES or any(_has_shell_expansion(arg) for arg in args):
        return _decision(
            "deny",
            manifest,
            mode=mode,
            reason_code="safe_command_shell_expansion_denied",
            scope=scope,
        )
    if exe == "env":
        return _decision("deny", manifest, mode=mode, reason_code="env_leak_denied", scope=scope)
    if exe not in _SAFE_COMMAND_EXECUTABLES:
        return _decision(
            "deny",
            manifest,
            mode=mode,
            reason_code="safe_command_executable_denied",
            scope=scope,
        )
    if exe == "git" and _git_subcommand(args) in _UNSAFE_GIT_SUBCOMMANDS:
        return _decision("deny", manifest, mode=mode, reason_code="unsafe_git", scope=scope)
    flag_denial = _readonly_argument_denial(exe, args, shell=False)
    if flag_denial is not None:
        return _decision(
            "deny",
            manifest,
            mode=mode,
            reason_code=flag_denial,
            scope=scope,
        )
    for arg in _path_like_command_args(exe, args):
        path_decision = _classify_path(arg, mutating=False, scope=scope)
        if path_decision.action == "deny":
            return _decision_for_path(path_decision, manifest, mode=mode, scope=scope)
    return _decision("allow", manifest, mode=mode, reason_code="safe_command_readonly", scope=scope)


def _classify_path(
    path: str,
    *,
    mutating: bool,
    scope: dict[str, object],
    patch_path: bool = False,
) -> _PathDecision:
    normalized = _normalize_relative(path)
    preview = f"path={normalized}"
    if path.startswith("/"):
        reason = "system_path_denied" if _is_system_absolute_path(path) else "absolute_path_denied"
        return _PathDecision("deny", reason, "[outside-workspace]", "path=[outside-workspace]")
    if _escapes_workspace(path):
        return _PathDecision(
            "deny",
            "path_escapes_workspace",
            "[outside-workspace]",
            "path=[outside-workspace]",
        )
    if mutating and _is_sealed_path(normalized):
        return _PathDecision("deny", "sealed_file_write_blocked", normalized, preview)
    if _is_protected_memory_path(normalized):
        return _PathDecision("deny", "protected_memory_path", normalized, preview)
    if _is_secret_path(normalized):
        if _scope_mode(scope) == "workspace_bypass" and not patch_path:
            return _PathDecision(
                "allow",
                "workspace_bypass_recorded_path_policy",
                normalized,
                "path=[workspace-secret-path-redacted]",
                path_policy_recorded=True,
            )
        return _PathDecision(
            "deny",
            "secret_path_denied",
            normalized,
            "path=[workspace-secret-path-redacted]",
        )
    return _PathDecision("allow", "workspace_safe", normalized, preview)


def _decision_for_path(
    path_decision: _PathDecision,
    manifest: ToolManifest,
    *,
    mode: RuntimeMode,
    scope: dict[str, object],
    preflight: dict[str, object] | None = None,
    status_metadata: dict[str, object] | None = None,
) -> RuntimeSafetyDecision:
    redacted_secret_path = (
        path_decision.path_policy_recorded or path_decision.reason_code == "secret_path_denied"
    )
    normalized = (
        "[workspace-secret-path-redacted]"
        if redacted_secret_path
        else path_decision.normalized
    )
    return _decision(
        path_decision.action,
        manifest,
        mode=mode,
        reason_code=path_decision.reason_code,
        scope=scope,
        normalized_workspace_relative=normalized,
        public_preview=path_decision.public_preview,
        path_policy_recorded=path_decision.path_policy_recorded,
        preflight=preflight,
        status_metadata=status_metadata,
    )


def _decision(
    action: SafetyAction,
    manifest: ToolManifest,
    *,
    mode: RuntimeMode,
    reason_code: str,
    scope: dict[str, object],
    normalized_workspace_relative: str | None = None,
    public_preview: str | None = None,
    path_policy_recorded: bool = False,
    preflight: dict[str, object] | None = None,
    status_metadata: dict[str, object] | None = None,
    policy_handled: bool = True,
) -> RuntimeSafetyDecision:
    metadata: dict[str, object] = {
        "toolName": manifest.name,
        "permissionClass": manifest.permission,
        "mode": mode,
        "runtimePermissionMode": scope["mode"],
        "runtimePermissionSource": scope["source"],
        "bypassRequested": scope["bypassRequested"],
        "dangerous": manifest.dangerous,
        "mutatesWorkspace": manifest.mutates_workspace,
        "reason": reason_code.replace("_", " "),
        "reasonCodes": (reason_code,),
        "securityPrecheck": "failed" if action == "deny" else "passed",
        "pathPolicyRecorded": path_policy_recorded,
        "policyHandled": policy_handled,
    }
    if normalized_workspace_relative is not None:
        metadata["normalizedWorkspaceRelative"] = normalized_workspace_relative
    if public_preview is not None:
        metadata["publicPreview"] = _redact_preview(public_preview)
    if preflight is not None:
        metadata["preflight"] = preflight
    if status_metadata is not None:
        metadata["statusMetadata"] = status_metadata
    if _selected_full_toolhost_scope(scope) and action == "allow":
        metadata["selectedFullToolhostPreapproved"] = True
    return RuntimeSafetyDecision(action=action, reason=str(metadata["reason"]), metadata=metadata)


def _resolve_scope(context: ToolContext, *, runtime_mode: RuntimeMode) -> dict[str, object]:
    raw = context.permission_scope
    scope_mode = "plan" if runtime_mode == "plan" else "default"
    source = "builtin"
    if isinstance(raw, str):
        scope_mode = _normalize_scope_token(raw)
    elif isinstance(raw, dict):
        mode_value = raw.get("mode") or raw.get("permissionMode") or raw.get("permission_mode")
        source_value = raw.get("source")
        if isinstance(mode_value, str):
            scope_mode = _normalize_scope_token(mode_value)
        if isinstance(source_value, str):
            source = _normalize_source_token(source_value)

    return {
        "mode": scope_mode,
        "source": source,
        "bypassRequested": scope_mode in {"bypass", "workspace_bypass"},
        "readLedger": getattr(context, "read_ledger", None),
        "sessionId": context.session_id,
        "workspaceRef": context.workspace_ref,
    }


def _normalize_scope_token(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized in {
        "auto",
        "default",
        "bypass",
        "workspace_bypass",
        "plan",
        "selected_full_toolhost",
    }:
        return normalized
    return "default"


def _normalize_source_token(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized in {
        "builtin",
        "mcp",
        "shell",
        "workspace",
        "child_agent",
        "selected_full_toolhost",
        "runtime",
    }:
        return normalized
    return "builtin"


def _scope_mode(scope: dict[str, object]) -> str:
    return str(scope.get("mode", "default"))


def _selected_full_toolhost_scope(scope: dict[str, object]) -> bool:
    return (
        _scope_mode(scope) == "selected_full_toolhost"
        and str(scope.get("source", "builtin")) == "selected_full_toolhost"
    )


def _selected_full_toolhost_status_metadata() -> dict[str, object]:
    return {
        "status": "ready",
        "selectedFullToolhostPreapproved": True,
        "hardSafetyStillEnforced": True,
        "metadataOnly": True,
    }


def _first_string(arguments: dict[str, object], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = arguments.get(name)
        if isinstance(value, str):
            return value
    return None


def _truthy(value: object) -> bool:
    return value is True or value == "true" or value == "1"


def _normalize_relative(path: str) -> str:
    path = path.strip().replace("\\", "/")
    normalized = posixpath.normpath(path)
    return "" if normalized == "." else normalized


def _escapes_workspace(path: str) -> bool:
    normalized = _normalize_relative(path)
    slash_path = path.replace("\\", "/")
    return normalized == ".." or normalized.startswith("../") or "/../" in f"/{slash_path}/"


def _is_system_absolute_path(path: str) -> bool:
    return path in _SYSTEM_ABSOLUTE_PATHS or any(
        path.startswith(prefix) for prefix in _SYSTEM_ABSOLUTE_PREFIXES
    )


def _is_sealed_path(path: str) -> bool:
    return path.rsplit("/", 1)[-1] in _SEALED_BASENAMES


def _is_protected_memory_path(path: str) -> bool:
    return path == "memory" or path.startswith("memory/")


def _is_secret_path(path: str) -> bool:
    lowered = path.lower()
    basename = lowered.rsplit("/", 1)[-1]
    return (
        basename in _SECRET_BASENAMES
        or any(f"/{lowered}".endswith(suffix) for suffix in _SECRET_SUFFIXES)
        or _SECRET_NAME_RE.search(path) is not None
    )


def _looks_like_path(value: str) -> bool:
    return (
        "/" in value
        or value.startswith(".")
        or value.endswith((".md", ".py", ".ts", ".tsx", ".json", ".txt", ".yml", ".yaml"))
    )


def _extract_patch_paths(patch: str) -> tuple[str, ...]:
    paths: list[str] = []
    for raw_line in patch.splitlines():
        line = raw_line.strip()
        for prefix in (
            "*** Update File: ",
            "*** Add File: ",
            "*** Delete File: ",
            "*** Move to: ",
        ):
            if line.startswith(prefix):
                paths.append(line.removeprefix(prefix).strip())
        if line.startswith("--- ") or line.startswith("+++ "):
            candidate = line[4:].strip().split("\t", 1)[0]
            if candidate != "/dev/null":
                paths.append(_normalize_diff_path(candidate))
    return tuple(path for path in paths if path)


def _normalize_diff_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    if normalized.startswith("a/") or normalized.startswith("b/"):
        normalized = normalized[2:]
    return normalized


def _is_destructive_shell(lowered_command: str) -> bool:
    destructive_patterns = (
        r"\brm\s+(?:-[a-zA-Z]*[rf][a-zA-Z]*|-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*)\s+/(?:\s|$|\*)",
        r"\bdd\b.+\bof=/dev/",
        r"\bmkfs(?:\.[a-z0-9]+)?\b",
        r"\bdiskutil\s+erase",
        r"\bchmod\s+-r\s+777\s+/",
        r"\bchown\s+-r\b.+\s+/",
    )
    return any(re.search(pattern, lowered_command) for pattern in destructive_patterns)


def _touches_system_boundary(lowered_command: str) -> bool:
    return any(
        fragment in lowered_command
        for fragment in ("/dev/", "/etc/", "/var/lib/", "/usr/", "/system/")
    ) or lowered_command.startswith("sudo ")


def _is_curl_pipe_exec(lowered_command: str) -> bool:
    return bool(
        re.search(r"\b(?:curl|wget)\b.+\|\s*(?:sudo\s+)?(?:ba)?sh\b", lowered_command)
    )


def _has_inline_interpreter_code(command: str) -> bool:
    for parts in _parsed_command_segments(command):
        if not parts:
            continue
        exe, args = _unwrap_command_executable(parts)
        flags = _inline_code_flags_for_executable(exe)
        if not flags:
            continue
        for arg in args:
            if arg in flags or any(
                arg.startswith(f"{flag}=") or _attached_short_flag(arg, flag)
                for flag in flags
            ):
                return True
        if exe in {"deno"} and args and args[0] == "eval":
            return True
    return False


def _unwrap_command_executable(parts: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    exe = _command_basename(parts[0])
    args = parts[1:]
    if exe == "env":
        index = 0
        while index < len(args):
            arg = args[index]
            if "=" in arg and not arg.startswith("-"):
                index += 1
                continue
            if arg in {"-i", "-0", "--ignore-environment", "--null"}:
                index += 1
                continue
            if arg in {"-u", "--unset"} and index + 1 < len(args):
                index += 2
                continue
            if arg.startswith("--unset="):
                index += 1
                continue
            if arg in {"-S", "--split-string"} and index + 1 < len(args):
                try:
                    split_args = tuple(shlex.split(args[index + 1]))
                except ValueError:
                    return exe, ()
                combined_args = split_args + args[index + 2 :]
                if not combined_args:
                    return exe, ()
                return _command_basename(combined_args[0]), combined_args[1:]
            if arg.startswith("-S") and arg != "-S":
                try:
                    split_args = tuple(shlex.split(arg[2:]))
                except ValueError:
                    return exe, ()
                combined_args = split_args + args[index + 1 :]
                if not combined_args:
                    return exe, ()
                return _command_basename(combined_args[0]), combined_args[1:]
            if arg.startswith("--split-string="):
                try:
                    split_args = tuple(shlex.split(arg.split("=", 1)[1]))
                except ValueError:
                    return exe, ()
                combined_args = split_args + args[index + 1 :]
                if not combined_args:
                    return exe, ()
                return _command_basename(combined_args[0]), combined_args[1:]
            exe = _command_basename(arg)
            return exe, args[index + 1 :]
        return exe, ()
    return exe, args


def _inline_code_flags_for_executable(exe: str) -> set[str]:
    if re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", exe):
        return {"-c"}
    return _INLINE_CODE_FLAGS_BY_EXECUTABLE.get(exe, set())


def _attached_short_flag(arg: str, flag: str) -> bool:
    return len(flag) == 2 and flag.startswith("-") and arg.startswith(flag) and arg != flag


def _is_unsafe_git_shell(command: str) -> bool:
    for parts in _parsed_command_segments(command):
        if parts and parts[0] == "git" and _git_subcommand(tuple(parts[1:])) in _UNSAFE_GIT_SUBCOMMANDS:
            return True
    return False


def _has_network_command(command: str) -> bool:
    segments = _parsed_command_segments(command)
    if not segments:
        return bool(re.search(r"\b(?:curl|ftp|nc|rsync|scp|sftp|ssh|wget)\b", command))
    return any(
        bool(parts and _command_basename(parts[0]) in _NETWORK_EXECUTABLES)
        for parts in segments
    )


def _has_network_exfiltration_command(command: str) -> bool:
    for index, parts in enumerate(_parsed_command_segments(command)):
        if not parts:
            continue
        exe = _command_basename(parts[0])
        if exe not in _NETWORK_EXECUTABLES:
            continue
        if index > 0 and "|" in command:
            return True
        if exe in {"rsync", "scp", "sftp"}:
            return True
        for arg in parts[1:]:
            if _is_network_upload_argument(arg):
                return True
    return False


def _is_simple_readonly_shell(command: str) -> bool:
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if not parts:
        return False
    if _has_complex_shell_operator(command):
        return False
    if parts[0] == "git":
        return _git_subcommand(tuple(parts[1:])) in _READONLY_GIT_SUBCOMMANDS
    return parts[0] in _READONLY_SHELL_COMMANDS


def _readonly_shell_denial(
    command: str,
    *,
    scope: dict[str, object],
) -> _PathDecision | str | None:
    if (
        _selected_full_toolhost_scope(scope)
        and _has_complex_shell_operator(command)
        and _trusted_local_shell_enabled()
        and _complex_command_is_read_safe(command)
        and _complex_command_paths_allowed(command, scope=scope)
    ):
        # Each segment was already validated read-safe with allowed paths. The
        # single-command flag/path checks below operate on the flattened command
        # (e.g. ``head -40`` looks like an unknown grep flag), so skip them for a
        # vetted trusted-local complex command instead of false-denying.
        return None
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    if not parts:
        return None
    exe = parts[0].rsplit("/", 1)[-1]
    if exe not in _READONLY_SHELL_COMMANDS and exe != "git":
        return None
    args = tuple(parts[1:])
    if exe == "git":
        subcommand = _git_subcommand(args)
        if subcommand not in _READONLY_GIT_SUBCOMMANDS:
            return "unsafe_git" if subcommand in _UNSAFE_GIT_SUBCOMMANDS else None
    flag_denial = _readonly_argument_denial(exe, args, shell=True)
    if flag_denial is not None:
        return flag_denial
    for arg in _path_like_command_args(exe, args):
        if _has_shell_path_expansion(arg):
            return "shell_path_expansion_denied"
    if _has_complex_shell_operator(command):
        return None
    for arg in _path_like_command_args(exe, args):
        path_decision = _classify_path(arg, mutating=False, scope=scope)
        if path_decision.action == "deny":
            return path_decision
    return None


def _readonly_argument_denial(exe: str, args: tuple[str, ...], *, shell: bool) -> str | None:
    for arg in args:
        if exe == "sed" and (arg == "-i" or arg.startswith("-i") or arg == "--in-place"):
            return "mutating_shell_flag_denied" if shell else "mutating_command_flag_denied"
        if arg in _MUTATING_FLAGS_BY_EXECUTABLE.get(exe, set()):
            return "mutating_shell_flag_denied" if shell else "mutating_command_flag_denied"
        if arg.startswith("--in-place="):
            return "mutating_shell_flag_denied" if shell else "mutating_command_flag_denied"
    for arg in args:
        if not arg.startswith("-") or arg == "-":
            continue
        if exe == "git" or _readonly_flag_allowed(exe, arg):
            continue
        return "unsafe_command_flag_denied"
    return None


def _readonly_flag_allowed(exe: str, arg: str) -> bool:
    if arg.startswith("--") or exe in {"find", "jq", "sed"}:
        return arg.split("=", 1)[0] in _READONLY_FLAGS_BY_EXECUTABLE.get(exe, set())
    allowed = _READONLY_FLAGS_BY_EXECUTABLE.get(exe, set())
    return arg in allowed or all(f"-{char}" in allowed for char in arg[1:])


def _path_like_command_args(exe: str, args: tuple[str, ...]) -> tuple[str, ...]:
    if exe == "git":
        args = _git_path_relevant_args(args)
    return tuple(arg for arg in args if not arg.startswith("-") and _looks_like_path(arg))


def _git_subcommand(args: tuple[str, ...]) -> str | None:
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"-C", "-c", "--git-dir", "--work-tree"}:
            index += 2
            continue
        if arg.startswith(("--git-dir=", "--work-tree=")):
            index += 1
            continue
        if arg.startswith("-"):
            index += 1
            continue
        return arg
    return None


def _git_path_relevant_args(args: tuple[str, ...]) -> tuple[str, ...]:
    paths: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"-C", "--git-dir", "--work-tree"} and index + 1 < len(args):
            paths.append(args[index + 1])
            index += 2
            continue
        if arg.startswith("--git-dir=") or arg.startswith("--work-tree="):
            paths.append(arg.split("=", 1)[1])
        index += 1
    return tuple(paths)


def _parsed_command_segments(command: str) -> tuple[tuple[str, ...], ...]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = tuple(lexer)
    except ValueError:
        return ()

    parsed: list[tuple[str, ...]] = []
    segment: list[str] = []
    for token in tokens:
        if token in {"|", ";", "&&", "||"}:
            if segment:
                parsed.append(tuple(segment))
                segment = []
            continue
        segment.append(token)
    if segment:
        parsed.append(tuple(segment))
    return tuple(parsed)


def _command_basename(command_token: str) -> str:
    return command_token.rsplit("/", 1)[-1]


_SHELL_UNDECOMPOSABLE = ("$(", "${", "`", ">", "<", ">>", "<<")


def _decompose_shell_segments(command: str) -> list[str] | None:
    """Split a command into top-level segments on ``| ; && ||``.

    Returns ``None`` (caller must deny) when the command contains command
    substitution, backticks, parameter expansion, or redirection. Operators
    inside single/double quotes are ignored.
    """
    if re.search(r"\$[A-Za-z_{]", command):
        return None  # variable / parameter expansion
    if "\n" in command or "\r" in command:
        return None  # newline is a shell command separator
    if any(token in command for token in _SHELL_UNDECOMPOSABLE):
        return None
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        matched = ""
        for op in ("&&", "||"):
            if command.startswith(op, i):
                matched = op
                break
        if not matched and ch in ("|", ";"):
            matched = ch
        if matched:
            segments.append("".join(buf).strip())
            buf = []
            i += len(matched)
            continue
        buf.append(ch)
        i += 1
    if quote is not None:
        return None
    tail = "".join(buf).strip()
    if tail:
        segments.append(tail)
    return [s for s in segments if s]


_NUMERIC_FLAG_COMMANDS = {"head", "tail"}


def _segment_is_read_safe(segment: str) -> bool:
    """True iff a single (already-decomposed) command segment is read-only safe:
    executable in the read-only allowlist, no write/mutating flags, no inline
    interpreter, non-empty.

    POSIX numeric-only short flags (e.g. ``head -30``, ``tail -5``) are
    treated as read-safe line/byte counts and skipped before the general
    flag-denial check.
    """
    segment = segment.strip()
    if not segment or _has_inline_interpreter_code(segment):
        return False
    try:
        parts = shlex.split(segment)
    except ValueError:
        return False
    if not parts:
        return False
    exe = parts[0].rsplit("/", 1)[-1]
    if exe not in _READONLY_SHELL_COMMANDS:
        return False
    # Strip POSIX numeric-only short flags (e.g. -30, -5) for line-count
    # commands before passing to the general flag-denial check.
    filtered_args = tuple(
        arg for arg in parts[1:]
        if not (exe in _NUMERIC_FLAG_COMMANDS and arg.startswith("-") and arg[1:].isdigit())
    )
    if _readonly_argument_denial(exe, filtered_args, shell=False) is not None:
        return False
    return True


def _complex_command_is_read_safe(command: str) -> bool:
    """True iff every top-level segment of a pipe/compound command is read-safe.

    Returns ``False`` when the command cannot be safely decomposed (command
    substitution, parameter expansion, redirection, etc.) or when any segment is
    not read-only safe.
    """
    segments = _decompose_shell_segments(command)
    if segments is None:
        return False
    return all(_segment_is_read_safe(seg) for seg in segments)


def _trusted_local_shell_enabled() -> bool:
    from magi_agent.config.env import parse_trusted_local_shell_enabled  # noqa: PLC0415

    return parse_trusted_local_shell_enabled(os.environ)


def _complex_command_paths_allowed(command: str, *, scope: dict[str, object]) -> bool:
    """True iff every segment's path args clear sealed/secret/escape classification.

    ``_complex_command_is_read_safe`` only vets executables and flags; it does not
    run path classification, so a pipeline such as ``cat .env.local | head`` would
    otherwise bypass the sealed/secret-path protection that the single-command
    ``_readonly_shell_denial`` enforces. This re-applies just the path checks
    (shell expansion + ``_classify_path`` deny) per segment so the trusted-local
    allowance never reads a protected or escaping path. Flag/executable safety is
    already guaranteed by ``_complex_command_is_read_safe`` (which, unlike
    ``_readonly_shell_denial``, correctly treats ``head -40`` style numeric flags
    as read-safe), so flag denial is intentionally not re-checked here.
    """
    segments = _decompose_shell_segments(command)
    if segments is None:
        return False
    for segment in segments:
        try:
            parts = shlex.split(segment)
        except ValueError:
            return False
        if not parts:
            return False
        exe = parts[0].rsplit("/", 1)[-1]
        args = tuple(parts[1:])
        for arg in _path_like_command_args(exe, args):
            if _has_shell_path_expansion(arg):
                return False
            if _classify_path(arg, mutating=False, scope=scope).action == "deny":
                return False
    return True


def _has_complex_shell_operator(command: str) -> bool:
    return (
        "\n" in command
        or "\r" in command
        or _SHELL_BACKGROUND_OPERATOR_RE.search(command) is not None
        or any(token in command for token in _SHELL_COMPLEX_TOKENS)
    )


def _has_shell_path_expansion(value: str) -> bool:
    return value.startswith("~") or _SHELL_VARIABLE_EXPANSION_RE.search(value) is not None


def _has_shell_path_expansion_command(command: str) -> bool:
    return any(
        _has_shell_path_expansion(arg)
        for parts in _parsed_command_segments(command)
        for arg in parts[1:]
    )


def _has_shell_expansion(value: str) -> bool:
    return any(token in value for token in ("|", ">", "<", ";", "&&", "||", "`", "$(", "${"))


def _is_network_upload_argument(arg: str) -> bool:
    if arg in _NETWORK_UPLOAD_FLAGS:
        return True
    if "=" in arg and arg.split("=", 1)[0] in _NETWORK_UPLOAD_FLAGS:
        return True
    return any(
        arg.startswith(flag) and arg != flag
        for flag in ("-d", "-F", "-T")
    )


def _preview_command(command: str) -> str:
    redacted = re.sub(r"Bearer\s+\S+", "Bearer [redacted]", command, flags=re.IGNORECASE)
    redacted = re.sub(r"rm\s+-rf\s+/", "rm -rf [redacted-path]", redacted, flags=re.IGNORECASE)
    return f"command={redacted}"


def _redact_preview(preview: str) -> str:
    redacted = re.sub(r"Bearer\s+\S+", "Bearer [redacted]", preview, flags=re.IGNORECASE)
    if ".env" in redacted or "secret" in redacted.lower() or "token" in redacted.lower():
        if redacted.startswith("path="):
            return "path=[workspace-secret-path-redacted]"
    return redacted


def _preflight(
    preflight_passed: bool,
    error_code: str | None,
    *,
    dry_run: bool = False,
    changed_files: tuple[str, ...] = (),
    hunks: int = 0,
    read_ledger: dict[str, object] | None = None,
) -> dict[str, object]:
    data: dict[str, object] = {
        "dryRun": dry_run,
        "preflightPassed": preflight_passed,
        "changedFiles": changed_files,
        "createdFiles": (),
        "deletedFiles": (),
        "hunks": hunks,
        "errorCode": error_code,
    }
    if read_ledger is not None:
        data["readLedger"] = read_ledger
    return data


def _read_ledger_preflight(
    *,
    context_read_ledger: object,
    context_session_id: object,
    context_workspace_ref: object,
    path: str,
    current_digest: str | None,
    mutation_kind: str,
) -> dict[str, object] | None:
    if context_read_ledger is None:
        return None
    if type(context_read_ledger) is not ReadLedger:
        return _read_ledger_context_invalid()
    if (
        not context_read_ledger.config.enabled
        or not context_read_ledger.config.local_in_memory_enabled
    ):
        return None
    if not isinstance(context_session_id, str) or not isinstance(context_workspace_ref, str):
        return _read_ledger_context_invalid()
    try:
        decision = context_read_ledger.require_fresh_full_read(
            WorkspaceMutationReadCheck(
                sessionId=context_session_id,
                workspaceRef=context_workspace_ref,
                path=path,
                currentDigest=current_digest,
                mutationKind=mutation_kind,
            ),
        )
    except (ValueError, ValidationError):
        return _read_ledger_context_invalid()
    if type(decision) is not WorkspaceMutationReadDecision:
        return _read_ledger_context_invalid()
    return decision.public_projection()


def _read_ledger_preflight_for_paths(
    *,
    context_read_ledger: object,
    context_session_id: object,
    context_workspace_ref: object,
    paths: tuple[str, ...],
    arguments: dict[str, object],
    mutation_kind: str,
) -> dict[str, object] | None:
    projections: list[dict[str, object]] = []
    for path in paths:
        projection = _read_ledger_preflight(
            context_read_ledger=context_read_ledger,
            context_session_id=context_session_id,
            context_workspace_ref=context_workspace_ref,
            path=path,
            current_digest=_current_digest_for_path(
                arguments,
                path,
                single_path=len(paths) == 1,
            ),
            mutation_kind=mutation_kind,
        )
        if projection is None:
            return None
        if projection.get("status") != "ok":
            return projection
        projections.append(projection)
    if not projections:
        return None
    if len(projections) == 1:
        return projections[0]
    return {
        "status": "ok",
        "reasonCodes": ("fresh_full_read",),
        "pathRefs": tuple(
            projection["pathRef"]
            for projection in projections
            if isinstance(projection.get("pathRef"), str)
        ),
        "entryRefs": tuple(
            projection["entryRef"]
            for projection in projections
            if isinstance(projection.get("entryRef"), str)
        ),
        "authorityFlags": {
            "readLedgerEnabled": True,
            "localInMemoryOnly": True,
            "productionWritesEnabled": False,
            "workspaceMutationAuthority": False,
        },
    }


def _current_digest_for_path(
    arguments: dict[str, object],
    path: str,
    *,
    single_path: bool,
) -> str | None:
    digest_map = arguments.get("currentDigests") or arguments.get("current_digests")
    if isinstance(digest_map, dict):
        value = digest_map.get(path)
        if isinstance(value, str):
            return value
        normalized_value = digest_map.get(_normalize_relative(path))
        if isinstance(normalized_value, str):
            return normalized_value
    if single_path:
        return _first_string(arguments, ("currentDigest", "current_digest", "digest"))
    return None


def _file_write_mutation_kind(arguments: dict[str, object]) -> str:
    raw = arguments.get("mutationKind") or arguments.get("mutation_kind") or arguments.get("operation")
    if isinstance(raw, str) and raw.strip().lower().replace("-", "_") == "create":
        return "create"
    if arguments.get("create") is True or arguments.get("ifMissing") == "create":
        return "create"
    return "replace"


def _read_ledger_block_decision(
    read_ledger_preflight: dict[str, object],
    path_decision: _PathDecision,
    manifest: ToolManifest,
    *,
    mode: RuntimeMode,
    scope: dict[str, object],
    changed_files: tuple[str, ...],
    dry_run: bool = False,
) -> RuntimeSafetyDecision:
    reason_code = _read_ledger_reason_code(read_ledger_preflight)
    return _decision_for_path(
        _PathDecision(
            "deny",
            reason_code,
            path_decision.normalized,
            path_decision.public_preview,
        ),
        manifest,
        mode=mode,
        scope=scope,
        preflight=_preflight(
            False,
            reason_code,
            dry_run=dry_run,
            changed_files=changed_files,
            read_ledger=read_ledger_preflight,
        ),
    )


def _read_ledger_reason_code(read_ledger_preflight: dict[str, object]) -> str:
    reason_codes = read_ledger_preflight.get("reasonCodes")
    return (
        str(reason_codes[0])
        if isinstance(reason_codes, list | tuple) and reason_codes
        else "read_ledger_preflight_blocked"
    )


def _read_ledger_context_invalid() -> dict[str, object]:
    return {
        "status": "blocked",
        "reasonCodes": ("read_ledger_context_invalid",),
        "pathRef": "path-ref:unavailable",
        "authorityFlags": {
            "readLedgerEnabled": True,
            "localInMemoryOnly": True,
            "productionWritesEnabled": False,
            "workspaceMutationAuthority": False,
        },
    }


def _bypass_status_metadata() -> dict[str, object]:
    return {
        "status": "blocked",
        "errorCode": "bypass_denied_hard_safety",
        "observable": True,
        "metadataOnly": True,
    }
