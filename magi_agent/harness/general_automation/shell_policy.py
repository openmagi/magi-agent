from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import re
import shlex
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from magi_agent.harness.general_automation.path_policy import (
    PathAccessRequest as _PathAccessRequest,
    classify_path_access as _classify_path_access,
)
from magi_agent.harness.general_automation.text_scrub import scrub_text as _scrub_text


ShellPolicyStatus = Literal["allowed", "approval_required", "denied"]
ShellReasonCode = str

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_CREDENTIAL_KEY_RE = re.compile(
    r"(?:TOKEN|SECRET|KEY|PASSWORD|COOKIE|CREDENTIAL|AUTH)",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"^(?:https?|ssh|git)://|^[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:")
_CURL_PIPE_RE = re.compile(r"\b(?:curl|wget)\b.+\|\s*(?:sudo\s+)?(?:ba)?sh\b", re.IGNORECASE)
_DESTRUCTIVE_RE = re.compile(
    r"(?:^|\s)(?:sudo\s+)?(?:/(?:[A-Za-z0-9_.-]+/)*rm|rm)\s+"
    r"(?=[^;&|]*(?:-[A-Za-z]*r[A-Za-z]*f[A-Za-z]*|-[A-Za-z]*f[A-Za-z]*r[A-Za-z]*|"
    r"--recursive|--force))",
    re.IGNORECASE,
)
_NETWORK_MUTATION_METHODS = {"post", "put", "patch", "delete"}
_PACKAGE_INSTALL_COMMANDS = {
    ("npm", "install"),
    ("npm", "add"),
    ("npm", "create"),
    ("pnpm", "install"),
    ("pnpm", "add"),
    ("pnpm", "create"),
    ("yarn", "add"),
    ("yarn", "create"),
    ("bun", "install"),
    ("bun", "add"),
    ("pip", "install"),
    ("pip3", "install"),
    ("pipx", "install"),
    ("poetry", "add"),
    ("uv", "sync"),
    ("uv", "lock"),
    ("uvx", ""),
    ("go", "get"),
}
_REDIRECTION_TOKENS = {">", ">>", "<", "2>", "2>>", "&>", "1>"}
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_:-]{0,96}$")

# Verb → operation_class for path policy cross-check.
# Commands not listed default to "read" (least-restrictive; policy still raises
# for blocked/external paths; only workspace reads stay silent).
_VERB_OPERATION_CLASS: dict[str, str] = {
    # Delete
    "rm": "delete",
    "rmdir": "delete",
    "unlink": "delete",
    # Write / create
    "cp": "write",
    "mv": "write",
    "touch": "write",
    "tee": "write",
    "install": "write",
    "ln": "write",
    "truncate": "write",
    "dd": "write",
    # Execute / permission-class mutations
    "chmod": "execute",
    "chown": "execute",
    "chgrp": "execute",
    # Read (explicit, also the fallback)
    "cat": "read",
    "less": "read",
    "more": "read",
    "head": "read",
    "tail": "read",
    "grep": "read",
    "diff": "read",
    "file": "read",
    "stat": "read",
    "wc": "read",
    # List
    "ls": "list",
    "find": "list",
    "du": "list",
    "tree": "list",
}


class ShellOutputBudgetMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    output_chars: int = Field(default=6000, alias="outputChars", ge=1, le=64_000)
    transcript_chars: int = Field(default=3000, alias="transcriptChars", ge=1, le=64_000)


class ShellPolicyRequest(BaseModel):
    model_config = _MODEL_CONFIG

    command: str = Field(repr=False)
    workspace_root: str = Field(alias="workspaceRoot", repr=False)
    env: Mapping[str, str] = Field(default_factory=dict, repr=False)
    timeout_ms: int = Field(default=60_000, alias="timeoutMs", ge=1, le=600_000)
    output_budget: ShellOutputBudgetMetadata = Field(
        default_factory=ShellOutputBudgetMetadata,
        alias="outputBudget",
    )

    @field_validator("command")
    @classmethod
    def _validate_command(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("command must be non-empty")
        if "\x00" in cleaned:
            raise ValueError("command must not contain NUL bytes")
        return cleaned

    @field_validator("workspace_root")
    @classmethod
    def _validate_workspace_root(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned.startswith("/"):
            raise ValueError("workspaceRoot must be an absolute path")
        return cleaned

    @field_serializer("command")
    def _serialize_command(self, value: str) -> str:
        return _digest(value)

    @field_serializer("workspace_root")
    def _serialize_workspace_root(self, value: str) -> str:
        return _digest(value)

    @field_serializer("env")
    def _serialize_env(self, value: Mapping[str, str]) -> dict[str, str]:
        return _env_projection(value)


class ShellPolicyAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    process_spawned: Literal[False] = Field(default=False, alias="processSpawned")
    shell_or_code_executed: Literal[False] = Field(default=False, alias="shellOrCodeExecuted")
    filesystem_write_attempted: Literal[False] = Field(
        default=False,
        alias="filesystemWriteAttempted",
    )
    network_accessed: Literal[False] = Field(default=False, alias="networkAccessed")
    live_tool_attached: Literal[False] = Field(default=False, alias="liveToolAttached")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        _ = update, deep
        return type(self)()


class ShellPolicyDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: ShellPolicyStatus
    command_digest: str = Field(alias="commandDigest")
    command_names: tuple[str, ...] = Field(default=(), alias="commandNames")
    redirections: tuple[str, ...] = ()
    path_arguments: tuple[str, ...] = Field(default=(), alias="pathArguments")
    network_commands: tuple[str, ...] = Field(default=(), alias="networkCommands")
    package_manager_commands: tuple[str, ...] = Field(default=(), alias="packageManagerCommands")
    destructive_commands: tuple[str, ...] = Field(default=(), alias="destructiveCommands")
    credential_env_assignments: tuple[str, ...] = Field(
        default=(),
        alias="credentialEnvAssignments",
    )
    reason_codes: tuple[ShellReasonCode, ...] = Field(alias="reasonCodes")
    timeout_ms: int = Field(alias="timeoutMs")
    output_budget: ShellOutputBudgetMetadata = Field(alias="outputBudget")
    env_projection: Mapping[str, str] = Field(alias="envProjection")
    authority_flags: ShellPolicyAuthorityFlags = Field(
        default_factory=ShellPolicyAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("command_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
            raise ValueError("commandDigest must be sha256")
        return value

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not _REASON_CODE_RE.fullmatch(item):
                raise ValueError("reason codes must be safe public identifiers")
        return value

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "commandDigest": self.command_digest,
            "commandNames": self.command_names,
            "redirections": self.redirections,
            "pathArguments": self.path_arguments,
            "networkCommands": self.network_commands,
            "packageManagerCommands": self.package_manager_commands,
            "destructiveCommands": self.destructive_commands,
            "credentialEnvAssignments": self.credential_env_assignments,
            "reasonCodes": self.reason_codes,
            "timeoutMs": self.timeout_ms,
            "outputBudget": self.output_budget.model_dump(by_alias=True, mode="json"),
            "envProjection": dict(self.env_projection),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True, mode="json"),
        }


def classify_shell_policy(request: ShellPolicyRequest) -> ShellPolicyDecision:
    tokens = _split_command(request.command)
    analysis_tokens = _tokens_without_env_assignments(tokens)
    command_names = _command_names(analysis_tokens)
    redirections = tuple(token for token in tokens if token in _REDIRECTION_TOKENS)
    path_arguments = _path_arguments(tokens)
    network_commands = _network_commands(analysis_tokens)
    package_manager_commands = _package_manager_commands(analysis_tokens)
    destructive_commands = ("rm",) if _DESTRUCTIVE_RE.search(request.command) else ()
    credential_env_assignments = _credential_env_assignments(tokens, request.env)
    reason_codes = _reason_codes(
        request.command,
        package_manager_commands=package_manager_commands,
        destructive_commands=destructive_commands,
        credential_env_assignments=credential_env_assignments,
    )

    # Cross-check extracted path targets against path_policy (PR13).
    # Only raises restriction — never lowers an existing decision.
    path_extra_codes = _path_policy_extra_reason_codes(
        tokens,
        analysis_tokens=analysis_tokens,
        workspace_root=request.workspace_root,
    )
    if path_extra_codes:
        reason_codes = tuple(dict.fromkeys((*reason_codes, *path_extra_codes)))

    status = _status_for(reason_codes)

    return ShellPolicyDecision(
        status=status,
        commandDigest=_digest(request.command),
        commandNames=command_names,
        redirections=redirections,
        pathArguments=path_arguments,
        networkCommands=network_commands,
        packageManagerCommands=package_manager_commands,
        destructiveCommands=destructive_commands,
        credentialEnvAssignments=credential_env_assignments,
        reasonCodes=reason_codes,
        timeoutMs=request.timeout_ms,
        outputBudget=request.output_budget,
        envProjection=_env_projection(request.env),
    )


def shell_policy_function_tool_metadata() -> dict[str, object]:
    return {
        "name": "GeneralAutomationShellRequest",
        "adkToolType": "FunctionTool",
        "enabledByDefault": False,
        "handlerAttached": False,
        "description": "Classify a shell command request without running it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "minLength": 1},
                "workspaceRoot": {"type": "string", "minLength": 1},
                "env": {"type": "object", "additionalProperties": {"type": "string"}},
                "timeoutMs": {"type": "integer", "minimum": 1, "maximum": 600000},
                "outputBudget": {
                    "type": "object",
                    "properties": {
                        "outputChars": {"type": "integer", "minimum": 1},
                        "transcriptChars": {"type": "integer", "minimum": 1},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["command", "workspaceRoot"],
            "additionalProperties": False,
        },
    }


def _split_command(command: str) -> tuple[str, ...]:
    try:
        return tuple(shlex.split(command, posix=True))
    except ValueError:
        return (command,)


def _tokens_without_env_assignments(tokens: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(token for token in tokens if not _is_env_assignment(token))


def _command_names(tokens: tuple[str, ...]) -> tuple[str, ...]:
    if not tokens:
        return ()
    return (tokens[0].split("/", 1)[-1],)


def _path_arguments(tokens: tuple[str, ...]) -> tuple[str, ...]:
    paths: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            paths.append(_safe_text(token))
            skip_next = False
            continue
        if token in _REDIRECTION_TOKENS:
            skip_next = True
            continue
        if "/" in token and not _URL_RE.search(token) and not _is_env_assignment(token):
            paths.append(_safe_text(token))
    return tuple(paths)


def _network_commands(tokens: tuple[str, ...]) -> tuple[str, ...]:
    if not tokens:
        return ()
    command = tokens[0].split("/", 1)[-1]
    if command in {"curl", "wget", "ssh", "scp", "rsync", "nc", "netcat", "socat"}:
        return (command,)
    if any(_URL_RE.search(token) for token in tokens):
        return (command,)
    return ()


def _package_manager_commands(tokens: tuple[str, ...]) -> tuple[str, ...]:
    if not tokens:
        return ()
    command = tokens[0].split("/", 1)[-1]
    subcommand = next((token for token in tokens[1:] if not token.startswith("-")), "")
    if (command, subcommand) in _PACKAGE_INSTALL_COMMANDS or (command, "") in _PACKAGE_INSTALL_COMMANDS:
        return (f"{command} {subcommand}".strip(),)
    if command == "python" and tuple(tokens[1:4]) == ("-m", "pip", "install"):
        return ("python -m pip install",)
    return ()


def _credential_env_assignments(
    tokens: tuple[str, ...],
    env: Mapping[str, str],
) -> tuple[str, ...]:
    keys = [token.split("=", 1)[0] for token in tokens if _is_env_assignment(token)]
    keys.extend(key for key in env if _CREDENTIAL_KEY_RE.search(key))
    return tuple(dict.fromkeys(keys))


def _reason_codes(
    command: str,
    *,
    package_manager_commands: tuple[str, ...],
    destructive_commands: tuple[str, ...],
    credential_env_assignments: tuple[str, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if _CURL_PIPE_RE.search(command):
        reasons.append("curl_pipe_exec_denied")
    if destructive_commands:
        reasons.append("destructive_filesystem_operation_denied")
    if credential_env_assignments:
        reasons.append("credential_env_assignment_denied")
    if package_manager_commands:
        reasons.append("package_install_requires_approval")
    if _is_network_mutation(command):
        reasons.append("network_mutation_requires_approval")
    return tuple(dict.fromkeys(reasons)) or ("safe_command_metadata_only",)


def _status_for(reason_codes: tuple[str, ...]) -> ShellPolicyStatus:
    if any(reason.endswith("_denied") for reason in reason_codes):
        if reason_codes == ("credential_env_assignment_denied", "package_install_requires_approval"):
            return "approval_required"
        if "package_install_requires_approval" in reason_codes and len(reason_codes) == 1:
            return "approval_required"
        return "denied"
    # path_target_blocked is a hard denial from the path gate (not _denied suffix).
    if "path_target_blocked" in reason_codes:
        return "denied"
    if any(reason.endswith("_requires_approval") for reason in reason_codes):
        return "approval_required"
    return "allowed"


def _is_network_mutation(command: str) -> bool:
    tokens = [token.casefold() for token in _split_command(command)]
    if not tokens or tokens[0].split("/", 1)[-1] != "curl":
        return False
    for index, token in enumerate(tokens):
        if token in {"-x", "--request"} and index + 1 < len(tokens):
            return tokens[index + 1] in _NETWORK_MUTATION_METHODS
        if token.startswith("-x") and len(token) > 2:
            return token[2:] in _NETWORK_MUTATION_METHODS
    return False


def _env_projection(env: Mapping[str, str]) -> dict[str, str]:
    projected: dict[str, str] = {}
    for key, value in env.items():
        projected[key] = "[redacted]" if _CREDENTIAL_KEY_RE.search(key) else _safe_text(str(value))
    return projected


def _raw_path_arguments(tokens: tuple[str, ...]) -> tuple[str, ...]:
    """Extract path-looking tokens without scrubbing — for path policy cross-check only.

    Mirrors the logic of ``_path_arguments`` but preserves the raw token values
    so they can be passed to ``classify_path_access``.  Redirect targets (after
    ``>``, ``>>`` etc.) are included; the verb itself and flag tokens are skipped.
    Only tokens containing '/' are extracted (same filter as the original path
    extractor), so command substitutions and globs without '/' (e.g. ``$(cmd)``,
    ``*.txt``) are never treated as path targets. Tokens with '/' are passed to
    ``classify_path_access``: relative ones canonicalize to workspace-local, absolute
    system paths (e.g. ``/etc/...``) are blocked.
    """
    paths: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            if "/" in token and not _URL_RE.search(token) and not _is_env_assignment(token):
                paths.append(token)
            skip_next = False
            continue
        if token in _REDIRECTION_TOKENS:
            skip_next = True
            continue
        if "/" in token and not _URL_RE.search(token) and not _is_env_assignment(token):
            paths.append(token)
    return tuple(paths)


def _infer_operation_class(analysis_tokens: tuple[str, ...]) -> str:
    """Infer path operation_class from the primary command verb."""
    if not analysis_tokens:
        return "read"
    verb = analysis_tokens[0].split("/", 1)[-1]
    return _VERB_OPERATION_CLASS.get(verb, "read")


def _path_policy_extra_reason_codes(
    tokens: tuple[str, ...],
    *,
    analysis_tokens: tuple[str, ...],
    workspace_root: str,
) -> tuple[str, ...]:
    """Cross-check extracted path targets against ``path_policy``.

    Returns additional reason codes to fold into the shell decision.  The logic
    is strictly additive — only raises restriction, never lowers it:

    - any path ``blocked``             → ``path_target_blocked``  (→ denied)
    - any ``external_directory`` or
      workspace-write needs approval   → ``path_target_requires_approval`` (→ approval_required)
    - workspace reads / lists         → no extra code (unchanged)

    Robust to tokens containing dynamic substitutions or globs: they are passed
    through to ``classify_path_access`` unchanged; the canonicalizer will resolve
    them to a non-workspace path and mark them accordingly.
    """
    raw_paths = _raw_path_arguments(tokens)
    if not raw_paths:
        return ()

    operation_class = _infer_operation_class(analysis_tokens)

    has_blocked = False
    has_approval = False

    for raw_path in raw_paths:
        try:
            path_request = _PathAccessRequest(
                workspaceRoot=workspace_root,
                path=raw_path,
                operationClass=operation_class,  # type: ignore[arg-type]
            )
            path_decision = _classify_path_access(path_request)
        except Exception:  # noqa: BLE001
            # If we can't parse the path (e.g. malformed token), treat conservatively.
            has_blocked = True
            continue

        if path_decision.status == "blocked":
            has_blocked = True
        elif path_decision.approval_required:
            has_approval = True

    extra: list[str] = []
    if has_blocked:
        extra.append("path_target_blocked")
    elif has_approval:
        extra.append("path_target_requires_approval")
    return tuple(extra)


def _is_env_assignment(token: str) -> bool:
    key, separator, _value = token.partition("=")
    return bool(separator and key and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key))


def _safe_text(value: str) -> str:
    return _scrub_text(value)


def _digest(value: str) -> str:
    return "sha256:" + sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "ShellOutputBudgetMetadata",
    "ShellPolicyAuthorityFlags",
    "ShellPolicyDecision",
    "ShellPolicyRequest",
    "classify_shell_policy",
    "shell_policy_function_tool_metadata",
]
