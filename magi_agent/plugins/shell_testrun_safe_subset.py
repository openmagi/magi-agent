from __future__ import annotations

from collections.abc import Mapping
import hashlib
import re
import shlex
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


ShellTestRunToolName = Literal["Bash", "TestRun"]
ShellTestRunStatus = Literal[
    "disabled",
    "blocked",
    "approval_required",
    "recorded_local_fake",
]
SafetyAction = Literal["allow", "ask", "deny", "not_evaluated"]
CommandClass = Literal[
    "readonly_shell",
    "test_runner",
    "network_shell",
    "unsafe_shell",
    "complex_shell",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_RECEIPT_RE = re.compile(r"^shell-testrun-receipt:[a-f0-9]{24}$")
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_:-]{0,96}$")
_PRIVATE_TEXT_RE = re.compile(
    r"(?:"
    r"authorization\s*:|set-cookie\s*:|\bcookie\b|\bbearer\s+[A-Za-z0-9._~+/=-]{6,}|"
    r"\bsid=[A-Za-z0-9._-]+|\bsk[-_][A-Za-z0-9._-]{6,}|gh[opusr]_[A-Za-z0-9_]{6,}|"
    r"github_pat_[A-Za-z0-9_]+|xox[a-z]-[A-Za-z0-9._-]+|AKIA[0-9A-Z]{8,}|"
    r"AIza[A-Za-z0-9_-]+|api[_-]?key\s*[:=]|password\s*[:=]|secret\s*[:=]|"
    r"token\s*[:=]|\b(?:auth|cookie|credential|credentials?|password|private|secret|"
    r"session|token)s?\b|private[_-]?key|"
    r"/Users(?:/|\b)|/home(?:/|\b)|/workspace(?:/|\b)|/data/bots(?:/|\b)|"
    r"/var/lib(?:/|\b)|/var/run(?:/|\b)|/etc(?:/|\b)|"
    r"raw[_ -]?(?:tool|child|prompt|transcript|output|result|log|args)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought"
    r")",
    re.IGNORECASE,
)
_PRIVATE_REF_RE = re.compile(
    r"(?:"
    r"authorization\s*:|\bcookie\b|\bbearer\s+[A-Za-z0-9._~+/=-]{6,}|"
    r"\bsk[-_][A-Za-z0-9._-]{6,}|gh[opusr]_[A-Za-z0-9_]{6,}|"
    r"github_pat_[A-Za-z0-9_]+|xox[a-z]-[A-Za-z0-9._-]+|AKIA[0-9A-Z]{8,}|"
    r"AIza[A-Za-z0-9_-]+|api[_-]?key\s*[:=]|password\s*[:=]|secret\s*[:=]|"
    r"token\s*[:=]|private[_-]?key|"
    r"/Users(?:/|\b)|/home(?:/|\b)|/workspace(?:/|\b)|/data/bots(?:/|\b)|"
    r"/var/lib(?:/|\b)|/var/run(?:/|\b)|/etc(?:/|\b)"
    r")",
    re.IGNORECASE,
)
_FALSE_AUTHORITY_OVERRIDES = {
    "processSpawned": False,
    "shellOrCodeExecuted": False,
    "filesystemWriteAttempted": False,
    "networkAccessed": False,
    "liveToolAttached": False,
    "routeAttached": False,
    "userVisibleOutputAllowed": False,
    "productionWriteAllowed": False,
}
_NO_POLICY_DECISION_REF = "policy:none"
_WRITE_CAPABLE_FLAGS = {
    "--basetemp",
    "--junitxml",
    "--override-ini",
    "--output",
}
_MAX_COMMAND_BYTES = 8000
_ALLOWED_POLICY_DECISION_REF_PREFIX = "policy:command-safe-subset"
_ALLOWED_POLICY_DECISION_REF_RE = re.compile(
    r"^policy:command-safe-subset(?::sha256:[a-f0-9]{64}|:[a-z0-9][a-z0-9_-]{0,63})?$"
)
_PRIVATE_RELATIVE_PATH_PARTS = {
    ".aws",
    ".docker",
    ".env",
    ".gnupg",
    ".kube",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".ssh",
    "credentials",
    "id_rsa",
    "id_ed25519",
    "known_hosts",
    "secret",
    "secrets",
    "token",
    "tokens",
}
_NETWORK_REASON_CODES = {
    "network_command_requires_approval",
    "network_exfiltration_denied",
    "network_command_not_in_safe_subset",
}
_ANSI_C_QUOTE_RE = re.compile(r"\$'")
_DYNAMIC_SHELL_EXPANSION_RE = re.compile(
    r"(?:"
    r"\$\(|"
    r"\$\{|"
    r"\$[@*#?!-]|"
    r"\$IFS\b|"
    r'\$"|'
    r"\{[^{}\r\n]*,[^{}\r\n]*\}"
    r")"
)
_LINE_CONTINUATION_RE = re.compile(r"\\\r?\n")
_PROCESS_SUBSTITUTION_RE = re.compile(r"[<>]\(")
_SHELL_FEED_RE = re.compile(r"<<")
_FORBIDDEN_SAFE_SUBSET_SYNTAX_RE = re.compile(r"[;&|<>`$(){}\"'\\\r\n]")
_SAFE_SHELL_TOKEN_RE = re.compile(r"^[A-Za-z0-9._/@:+,\-=]+/?$")
_SHELL_WORD_RECURSION_LIMIT = 2
_NETWORK_TOOL_NAMES = {
    "bunx",
    "curl",
    "ftp",
    "ncat",
    "nc",
    "netcat",
    "npx",
    "pnpx",
    "rsync",
    "scp",
    "sftp",
    "socat",
    "ssh",
    "telnet",
    "uvx",
    "wget",
}
_BLOCKING_REASON_CODES = {
    "curl_pipe_exec",
    "destructive_shell",
    "system_shell_denied",
    "unsafe_git",
    "path_escapes_workspace",
    "absolute_path_denied",
    "system_path_denied",
    "protected_memory_path",
    "secret_path_denied",
    "sealed_file_write_blocked",
    "shell_path_expansion_denied",
    "shell_ansi_c_quote_denied",
    "shell_dynamic_expansion_denied",
    "shell_heredoc_denied",
    "shell_line_continuation_denied",
    "shell_process_substitution_denied",
    "shell_command_outside_safe_subset",
    "mutating_shell_flag_denied",
    "unsafe_command_flag_denied",
}
_LOCAL_HARD_DENY_COMMAND_RE = re.compile(
    r"(?:^|[;&|]\s*|\b(?:command|bash\s+-lc|sh\s+-c)\s+[\"']?)"
    r"(?:sudo\s+)?(?:/(?:[A-Za-z0-9._-]+/)*rm|rm)\b(?=[^;&|]*(?:"
    r"\s-[A-Za-z]*r[A-Za-z]*f[A-Za-z]*\b|"
    r"\s-[A-Za-z]*f[A-Za-z]*r[A-Za-z]*\b|"
    r"\s-r\b[^;&|]*\s-f\b|"
    r"\s-f\b[^;&|]*\s-r\b|"
    r"\s--recursive\b[^;&|]*\s--force\b|"
    r"\s--force\b[^;&|]*\s--recursive\b|"
    r"\s-r\b[^;&|]*\s--force\b|"
    r"\s--force\b[^;&|]*\s-r\b|"
    r"\s--recursive\b[^;&|]*\s-f\b|"
    r"\s-f\b[^;&|]*\s--recursive\b))",
    re.IGNORECASE,
)
_CURL_PIPE_EXEC_RE = re.compile(
    r"\b(?:curl|wget)\b.+\|\s*(?:sudo\s+)?(?:ba)?sh\b",
    re.IGNORECASE,
)
_SYSTEM_OR_PRIVATE_PATH_RE = re.compile(
    r"(?:^|\s|[\"'<>])(?:/etc|/proc|/sys|/dev|/root|/var/lib|/var/run|/Users|/home|/workspace|"
    r"/data/bots)(?:/|\b)",
    re.IGNORECASE,
)
_PARENT_PATH_RE = re.compile(r"(?:^|[^\w.-])(?:\./)*[^;&|]*\.\.(?:/|\\)")
_SHELL_EXPANSION_RE = re.compile(
    r"(?:^|\s|[\"'<>])(?:~(?:/|\b)|\$\{|`|\$\(|\$(?:HOME|USER|PWD|OLDPWD|TMPDIR|"
    r"SSH_AUTH_SOCK|AWS_[A-Z0-9_]+|KUBECONFIG|OPENAI_[A-Z0-9_]+|SUPABASE_[A-Z0-9_]+))",
    re.IGNORECASE,
)
_NETWORK_COMMAND_RE = re.compile(
    r"(?:^|\s|[;&|])(?:curl|wget|ssh|scp|sftp|rsync|nc|netcat|telnet|socat|"
    r"ftp|npx|pnpx|bunx)\b|"
    r"(?:^|\s|[;&|])git\s+(?:-[A-Za-z0-9=._/-]+\s+)*(?:clone|fetch|pull|push|submodule)\b|"
    r"(?:^|\s|[;&|])gh\s+(?:repo\s+)?clone\b|"
    r"(?:^|\s|[;&|])(?:npm\s+(?:exec|create|init)|pnpm\s+(?:dlx|create)|"
    r"yarn\s+(?:dlx|create)|bun\s+x|uvx|pipx\s+run)\b|"
    r"(?:^|\s|[;&|])python(?:3)?\s+-m\s+http\.server\b|"
    r"(?:^|\s|[;&|])python(?:3)?\s+-m\s+pip\s+install\b|"
    r"(?:^|\s|[;&|])(?:pip|pip3|poetry|uv|npm|pnpm|yarn|go|brew)\s+"
    r"(?:install|add|get|publish|upgrade)\b|"
    r"https?://|ssh://|git@[A-Za-z0-9_.-]+:",
    re.IGNORECASE,
)
_UNSAFE_GIT_RE = re.compile(
    r"(?:^|\s|[;&|])git\s+(?:-[A-Za-z0-9=._/-]+\s+)*(?:add|apply|checkout|cherry-pick|clean|"
    r"commit|merge|mv|pull|push|rebase|reset|restore|rm|switch|tag)\b",
    re.IGNORECASE,
)


class ShellTestRunSafeSubsetConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_execution_enabled: bool = Field(
        default=False,
        alias="localFakeExecutionEnabled",
    )
    max_output_bytes: int = Field(default=6000, ge=1, le=64_000, alias="maxOutputBytes")
    max_transcript_bytes: int = Field(
        default=3000,
        ge=1,
        le=64_000,
        alias="maxTranscriptBytes",
    )
    production_execution_enabled: Literal[False] = Field(
        default=False,
        alias="productionExecutionEnabled",
    )


class ShellOutputBudget(BaseModel):
    model_config = _MODEL_CONFIG

    max_output_bytes: int = Field(ge=1, le=64_000, alias="maxOutputBytes")
    max_transcript_bytes: int = Field(ge=1, le=64_000, alias="maxTranscriptBytes")


class ShellCommandSafetyDecision(BaseModel):
    model_config = _MODEL_CONFIG

    action: SafetyAction
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    policy_decision_ref: str | None = Field(default=None, alias="policyDecisionRef")

    @field_validator("reason_codes")
    @classmethod
    def _sanitize_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        sanitized = tuple(_safe_reason_code(item) for item in value if str(item).strip())
        return sanitized or ("command_safety_decision_missing_reason",)

    @field_validator("policy_decision_ref")
    @classmethod
    def _safe_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not _is_allowed_policy_decision_ref(text):
            raise ValueError("policyDecisionRef must be command-safe-subset scoped")
        return text[:180]


class ShellTestRunAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    recipe_enabled: bool = Field(default=False, alias="recipeEnabled")
    local_fake_execution_enabled: bool = Field(
        default=False,
        alias="localFakeExecutionEnabled",
    )
    process_spawned: Literal[False] = Field(default=False, alias="processSpawned")
    shell_or_code_executed: Literal[False] = Field(
        default=False,
        alias="shellOrCodeExecuted",
    )
    filesystem_write_attempted: Literal[False] = Field(
        default=False,
        alias="filesystemWriteAttempted",
    )
    network_accessed: Literal[False] = Field(default=False, alias="networkAccessed")
    live_tool_attached: Literal[False] = Field(default=False, alias="liveToolAttached")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )
    production_write_allowed: Literal[False] = Field(
        default=False,
        alias="productionWriteAllowed",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values.update(_FALSE_AUTHORITY_OVERRIDES)
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
        data.update(_FALSE_AUTHORITY_OVERRIDES)
        return type(self).model_validate(data)

    @field_serializer(
        "process_spawned",
        "shell_or_code_executed",
        "filesystem_write_attempted",
        "network_accessed",
        "live_tool_attached",
        "route_attached",
        "user_visible_output_allowed",
        "production_write_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class ShellTestRunSafeSubsetRequest(BaseModel):
    model_config = _MODEL_CONFIG

    tool_name: ShellTestRunToolName = Field(alias="toolName")
    command: str = Field(repr=False)
    session_id: str = Field(alias="sessionId")
    workspace_ref: str = Field(alias="workspaceRef")
    turn_id: str = Field(alias="turnId")
    explicit_approval: bool = Field(default=False, alias="explicitApproval")
    safety_decision: ShellCommandSafetyDecision | None = Field(
        default=None,
        alias="safetyDecision",
    )
    max_output_bytes: int | None = Field(default=None, ge=1, le=64_000, alias="maxOutputBytes")
    max_transcript_bytes: int | None = Field(
        default=None,
        ge=1,
        le=64_000,
        alias="maxTranscriptBytes",
    )

    @field_validator("command")
    @classmethod
    def _non_empty_command(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("command must be non-empty")
        return text

    @field_validator("session_id", "workspace_ref", "turn_id")
    @classmethod
    def _safe_public_ref(cls, value: str) -> str:
        text = value.strip()
        if not text or _PRIVATE_REF_RE.search(text):
            raise ValueError("command safety refs must be sanitized")
        return text[:180]


class ShellTestRunMaterialization(BaseModel):
    model_config = _MODEL_CONFIG

    recipe_id: str = Field(
        default="openmagi.general-automation.shell-testrun-safe-subset",
        alias="recipeId",
    )
    tool_names: tuple[ShellTestRunToolName, ...] = Field(
        default=("Bash", "TestRun"),
        alias="toolNames",
    )
    approval_refs: tuple[str, ...] = Field(
        default=("approval:shell-command", "approval:test-run"),
        alias="approvalRefs",
    )
    evidence_refs: tuple[str, ...] = Field(
        default=(
            "evidence:command-safety-decision",
            "evidence:command-output-budget",
            "evidence:test-run-receipt",
        ),
        alias="evidenceRefs",
    )
    validator_callback_refs: tuple[str, ...] = Field(
        default=("validator:command-safe-subset",),
        alias="validatorCallbackRefs",
    )
    attachment_flags: Mapping[str, Literal[False]] = Field(alias="attachmentFlags")

    def public_projection(self) -> dict[str, object]:
        return {
            "recipeId": self.recipe_id,
            "toolNames": list(self.tool_names),
            "approvalRefs": list(self.approval_refs),
            "evidenceRefs": list(self.evidence_refs),
            "validatorCallbackRefs": list(self.validator_callback_refs),
            "attachmentFlags": _false_attachment_flags(),
        }


class ShellTestRunDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: ShellTestRunStatus
    tool_name: ShellTestRunToolName = Field(alias="toolName")
    command_digest: str = Field(alias="commandDigest")
    command_class: CommandClass = Field(alias="commandClass")
    safety_action: SafetyAction = Field(alias="safetyAction")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    receipt_ref: str = Field(alias="receiptRef")
    policy_decision_ref: str = Field(default=_NO_POLICY_DECISION_REF, alias="policyDecisionRef")
    output_budget: ShellOutputBudget = Field(alias="outputBudget")
    authority_flags: ShellTestRunAuthorityFlags = Field(alias="authorityFlags")

    @field_validator("command_digest")
    @classmethod
    def _safe_digest(cls, value: str) -> str:
        text = value.strip()
        if _DIGEST_RE.fullmatch(text) is None or _PRIVATE_TEXT_RE.search(text):
            raise ValueError("commandDigest must be a sanitized sha256 digest")
        return text

    @field_validator("reason_codes")
    @classmethod
    def _sanitize_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        sanitized = tuple(_safe_reason_code(item) for item in value if str(item).strip())
        return sanitized or ("command_decision_missing_reason",)

    @field_validator("policy_decision_ref")
    @classmethod
    def _safe_policy_ref(cls, value: str) -> str:
        text = value.strip()
        if text == _NO_POLICY_DECISION_REF:
            return text
        if not _is_allowed_policy_decision_ref(text):
            raise ValueError("policyDecisionRef must be command-safe-subset scoped")
        return text[:180]

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["authorityFlags"] = _coerce_authority_flags(values.get("authorityFlags"))
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
        data["authorityFlags"] = _coerce_authority_flags(data.get("authorityFlags"))
        return type(self).model_validate(data)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "toolName": self.tool_name,
            "commandDigest": _public_digest(self.command_digest),
            "commandClass": self.command_class,
            "safetyAction": self.safety_action,
            "reasonCodes": [_safe_reason_code(code) for code in self.reason_codes],
            "receiptRef": _public_receipt_ref(self.receipt_ref),
            "policyDecisionRef": _public_policy_ref(self.policy_decision_ref),
            "outputBudget": self.output_budget.model_dump(by_alias=True),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class ShellTestRunSafeSubsetBinding:
    """Recipe/plugin-owned Bash and TestRun policy binding without process launch."""

    def __init__(
        self,
        config: ShellTestRunSafeSubsetConfig | None = None,
    ) -> None:
        self.config = config or ShellTestRunSafeSubsetConfig()

    def materialize(self) -> ShellTestRunMaterialization:
        return ShellTestRunMaterialization(attachmentFlags=_false_attachment_flags())

    def evaluate(
        self,
        request: ShellTestRunSafeSubsetRequest | Mapping[str, object],
    ) -> ShellTestRunDecision:
        parsed = (
            request
            if isinstance(request, ShellTestRunSafeSubsetRequest)
            else ShellTestRunSafeSubsetRequest.model_validate(request)
        )
        flags = ShellTestRunAuthorityFlags(
            recipeEnabled=self.config.enabled,
            localFakeExecutionEnabled=self.config.local_fake_execution_enabled,
        )
        command_digest = _digest(parsed.command)
        output_budget = _output_budget(parsed, self.config)

        if _command_too_long(parsed.command):
            return _decision(
                parsed,
                "blocked",
                command_digest,
                "deny",
                ("command_too_long",),
                flags,
                output_budget=output_budget,
                command_class="unsafe_shell",
            )

        if not self.config.enabled:
            return _decision(
                parsed,
                "disabled",
                command_digest,
                "not_evaluated",
                ("shell_testrun_safe_subset_disabled",),
                flags,
                output_budget=output_budget,
            )

        hard_denial = _hard_command_denial(parsed.command)
        if hard_denial is not None:
            return _decision(
                parsed,
                "blocked",
                command_digest,
                "deny",
                (hard_denial,),
                flags,
                output_budget=output_budget,
                command_class="unsafe_shell",
            )

        if parsed.safety_decision is None:
            return _decision(
                parsed,
                "blocked",
                command_digest,
                "not_evaluated",
                ("command_safety_decision_required",),
                flags,
                output_budget=output_budget,
                command_class="complex_shell",
            )

        safety_action = parsed.safety_decision.action
        safety_reason_codes = parsed.safety_decision.reason_codes
        command_class = _command_class(parsed, safety_reason_codes)
        if safety_action == "deny":
            command_class = "unsafe_shell"

        if safety_action == "not_evaluated":
            return _decision(
                parsed,
                "blocked",
                command_digest,
                "not_evaluated",
                _append_reason(
                    safety_reason_codes,
                    "command_safety_decision_not_evaluated",
                ),
                flags,
                output_budget=output_budget,
                command_class=command_class,
            )

        if safety_action == "deny" or any(code in _BLOCKING_REASON_CODES for code in safety_reason_codes):
            return _decision(
                parsed,
                "blocked",
                command_digest,
                "deny",
                safety_reason_codes or ("command_safe_subset_denied",),
                flags,
                output_budget=output_budget,
                command_class=command_class,
            )

        if any(code in _NETWORK_REASON_CODES for code in safety_reason_codes):
            return _decision(
                parsed,
                "blocked",
                command_digest,
                "ask",
                ("network_command_not_in_safe_subset",),
                flags,
                output_budget=output_budget,
                command_class="network_shell",
            )

        safe_subset_denial = _strict_safe_subset_denial(parsed)
        if safe_subset_denial is not None:
            return _decision(
                parsed,
                "blocked",
                command_digest,
                "deny",
                (safe_subset_denial,),
                flags,
                output_budget=output_budget,
                command_class="unsafe_shell",
            )

        if parsed.safety_decision.policy_decision_ref is None:
            return _decision(
                parsed,
                "blocked",
                command_digest,
                safety_action,
                _append_reason(
                    safety_reason_codes,
                    "policy_decision_ref_required",
                ),
                flags,
                output_budget=output_budget,
                command_class=command_class,
            )

        if not parsed.explicit_approval:
            return _decision(
                parsed,
                "approval_required",
                command_digest,
                safety_action,
                _append_reason(
                    safety_reason_codes,
                    "shell_testrun_safe_subset_requires_approval",
                ),
                flags,
                output_budget=output_budget,
                command_class=command_class,
            )

        if not self.config.local_fake_execution_enabled:
            return _decision(
                parsed,
                "blocked",
                command_digest,
                safety_action,
                _append_reason(
                    safety_reason_codes,
                    "local_fake_execution_disabled",
                ),
                flags,
                output_budget=output_budget,
                command_class=command_class,
            )

        return _decision(
            parsed,
            "recorded_local_fake",
            command_digest,
            safety_action,
            _append_reason(safety_reason_codes, "local_fake_command_receipt_only"),
            flags,
            output_budget=output_budget,
            command_class=command_class,
        )


def _decision(
    request: ShellTestRunSafeSubsetRequest,
    status: ShellTestRunStatus,
    command_digest: str,
    safety_action: SafetyAction,
    reason_codes: tuple[str, ...],
    flags: ShellTestRunAuthorityFlags,
    *,
    output_budget: ShellOutputBudget,
    command_class: CommandClass | None = None,
) -> ShellTestRunDecision:
    safe_class = command_class or ("test_runner" if request.tool_name == "TestRun" else "complex_shell")
    policy_decision_ref = (
        request.safety_decision.policy_decision_ref
        if request.safety_decision is not None and request.safety_decision.policy_decision_ref is not None
        else _NO_POLICY_DECISION_REF
    )
    return ShellTestRunDecision(
        status=status,
        toolName=request.tool_name,
        commandDigest=command_digest,
        commandClass=safe_class,
        safetyAction=safety_action,
        reasonCodes=tuple(dict.fromkeys(reason_codes)),
        receiptRef=_receipt_ref(
            request=request,
            status=status,
            command_digest=command_digest,
            reason_codes=tuple(dict.fromkeys(reason_codes)),
            output_budget=output_budget,
            policy_decision_ref=policy_decision_ref,
        ),
        policyDecisionRef=policy_decision_ref,
        outputBudget=output_budget,
        authorityFlags=flags,
    )


def _command_class(
    request: ShellTestRunSafeSubsetRequest,
    reason_codes: tuple[str, ...],
) -> CommandClass:
    if any(code in _NETWORK_REASON_CODES for code in reason_codes):
        return "network_shell"
    if any(code in _BLOCKING_REASON_CODES for code in reason_codes):
        return "unsafe_shell"
    if request.tool_name == "TestRun":
        return "test_runner"
    if "safe_command_readonly" in reason_codes:
        return "readonly_shell"
    return "complex_shell"


def _append_reason(reason_codes: tuple[str, ...], reason: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*reason_codes, reason)))


def _hard_command_denial(command: str) -> str | None:
    if _LINE_CONTINUATION_RE.search(command):
        return "shell_line_continuation_denied"
    if _ANSI_C_QUOTE_RE.search(command):
        return "shell_ansi_c_quote_denied"
    if _SHELL_EXPANSION_RE.search(command):
        return "shell_path_expansion_denied"
    if _DYNAMIC_SHELL_EXPANSION_RE.search(command):
        return "shell_dynamic_expansion_denied"
    if _PROCESS_SUBSTITUTION_RE.search(command):
        return "shell_process_substitution_denied"
    if _SHELL_FEED_RE.search(command):
        return "shell_heredoc_denied"
    if (
        _LOCAL_HARD_DENY_COMMAND_RE.search(command)
        or _contains_rm_command(command)
        or _contains_find_delete(command)
    ):
        return "destructive_shell"
    if _CURL_PIPE_EXEC_RE.search(command):
        return "curl_pipe_exec"
    if _UNSAFE_GIT_RE.search(command):
        return "unsafe_git"
    if _NETWORK_COMMAND_RE.search(command) or _contains_network_command(command):
        return "network_command_not_in_safe_subset"
    if _PARENT_PATH_RE.search(command):
        return "path_escapes_workspace"
    if _SYSTEM_OR_PRIVATE_PATH_RE.search(command):
        return "system_path_denied"
    normalized_denial = _normalized_shell_path_denial(command)
    if normalized_denial is not None:
        return normalized_denial
    if _PRIVATE_TEXT_RE.search(command):
        return "command_sensitive_material_denied"
    return None


def _command_too_long(command: str) -> bool:
    return len(command.encode("utf-8")) > _MAX_COMMAND_BYTES


def _contains_rm_command(command: str, *, _depth: int = 0) -> bool:
    for word in _shell_words(command):
        if _command_basename(word) == "rm":
            return True
        if _depth < _SHELL_WORD_RECURSION_LIMIT and " " in word:
            if _contains_rm_command(word, _depth=_depth + 1):
                return True
    return False


def _contains_find_delete(command: str, *, _depth: int = 0) -> bool:
    words = _shell_words(command)
    for index, word in enumerate(words):
        remaining = tuple(_command_basename(item) for item in words[index + 1 :])
        if _command_basename(word) == "find" and "-delete" in remaining:
            return True
        if _depth < _SHELL_WORD_RECURSION_LIMIT and " " in word:
            if _contains_find_delete(word, _depth=_depth + 1):
                return True
    return False


def _contains_network_command(command: str, *, _depth: int = 0) -> bool:
    words = _shell_words(command)
    for index, word in enumerate(words):
        base = _command_basename(word)
        remaining = tuple(_command_basename(item) for item in words[index + 1 :])

        if base in _NETWORK_TOOL_NAMES:
            return True
        if base in {"python", "python3"} and _python_invokes_network_module(remaining):
            return True
        if base == "git" and _has_any(remaining, {"clone", "fetch", "pull", "push", "submodule"}):
            return True
        if base == "gh" and _has_any(remaining, {"clone"}):
            return True
        if base == "npm" and _has_any(remaining, {"add", "create", "exec", "init", "install", "publish", "upgrade"}):
            return True
        if base in {"pnpm", "yarn"} and _has_any(remaining, {"add", "create", "dlx", "install", "publish", "upgrade"}):
            return True
        if base == "bun" and _has_any(remaining, {"add", "create", "install", "x"}):
            return True
        if base in {"pip", "pip3"} and _has_any(remaining, {"install"}):
            return True
        if base == "pipx" and _has_any(remaining, {"inject", "install", "reinstall", "run", "upgrade"}):
            return True
        if base == "find" and "-delete" in remaining:
            return True
        if base == "poetry" and _has_any(remaining, {"add", "install", "publish", "update"}):
            return True
        if base == "uv" and (
            _has_any(remaining, {"add", "install", "lock", "publish", "run", "sync", "tool"})
            or "--with" in remaining
            or "--from" in remaining
            or "--with-requirements" in remaining
        ):
            return True
        if base == "go" and _has_any(remaining, {"get", "install"}):
            return True
        if base == "brew" and _has_any(remaining, {"install", "upgrade"}):
            return True
        if _depth < _SHELL_WORD_RECURSION_LIMIT and " " in word:
            if _contains_network_command(word, _depth=_depth + 1):
                return True
    return False


def _python_invokes_network_module(words: tuple[str, ...]) -> bool:
    for index, word in enumerate(words):
        if word != "-m" or index + 1 >= len(words):
            continue
        module = words[index + 1]
        if module == "http.server":
            return True
        if module == "pip" and _has_any(words[index + 2 :], {"install"}):
            return True
    return False


def _normalized_shell_path_denial(command: str) -> str | None:
    normalized = " ".join(_shell_words(command))
    if not normalized or normalized == command:
        return None
    if _SHELL_EXPANSION_RE.search(normalized):
        return "shell_path_expansion_denied"
    if _PARENT_PATH_RE.search(normalized):
        return "path_escapes_workspace"
    if _SYSTEM_OR_PRIVATE_PATH_RE.search(normalized):
        return "system_path_denied"
    return None


def _strict_safe_subset_denial(request: ShellTestRunSafeSubsetRequest) -> str | None:
    if _FORBIDDEN_SAFE_SUBSET_SYNTAX_RE.search(request.command):
        return "shell_command_outside_safe_subset"

    words = _shell_words(request.command)
    if not words:
        return "shell_command_outside_safe_subset"

    base = words[0].lower()
    if request.tool_name == "Bash":
        if base == "pwd" and len(words) == 1:
            return None
        if base == "ls" and _all_safe_readonly_args(words[1:]):
            return None
        if base == "git" and len(words) >= 2:
            subcommand = words[1].lower()
            if subcommand in {"diff", "log", "show", "status"} and _all_safe_readonly_args(words[2:]):
                return None
        return "shell_command_outside_safe_subset"

    if request.tool_name == "TestRun":
        if base in {"python", "python3"} and len(words) >= 3:
            if words[1] == "-m" and words[2] == "pytest" and _all_safe_readonly_args(words[3:]):
                return None
        if base == "pytest" and _all_safe_readonly_args(words[1:]):
            return None
        return "shell_command_outside_safe_subset"

    return "shell_command_outside_safe_subset"


def _all_safe_readonly_args(words: tuple[str, ...]) -> bool:
    return all(_is_safe_readonly_arg(word) for word in words)


def _is_safe_readonly_arg(word: str) -> bool:
    if not word or _FORBIDDEN_SAFE_SUBSET_SYNTAX_RE.search(word):
        return False
    if word.startswith("-") and word != "--":
        return False
    flag, _, value = word.partition("=")
    if flag in _WRITE_CAPABLE_FLAGS:
        return False
    if value and not _is_safe_readonly_arg(value):
        return False
    if word.startswith("/"):
        return False
    if word.startswith("./") and word.count("/") >= 1:
        return False
    if word.startswith(".git/") or "/.git/" in word:
        return False
    if _contains_private_relative_path_part(word):
        return False
    if word in {".", "--"}:
        return True
    if any(part == ".." for part in word.split("/")):
        return False
    return _SAFE_SHELL_TOKEN_RE.fullmatch(word) is not None


def _contains_private_relative_path_part(word: str) -> bool:
    candidates = [word]
    if ":" in word:
        candidates.extend(part for part in word.split(":") if part)
    for candidate in candidates:
        parts = tuple(part.lower() for part in candidate.split("/") if part)
        if any(
            part.startswith(".env")
            or (part.startswith(".") and part not in {".", ".."})
            or part in _PRIVATE_RELATIVE_PATH_PARTS
            or part.endswith(".pem")
            or part.endswith(".key")
            for part in parts
        ):
            return True
    return False


def _is_allowed_policy_decision_ref(text: str) -> bool:
    return bool(text and _PRIVATE_REF_RE.search(text) is None and _ALLOWED_POLICY_DECISION_REF_RE.fullmatch(text))


def _shell_words(command: str) -> tuple[str, ...]:
    try:
        return tuple(shlex.split(command, posix=True))
    except ValueError:
        return tuple(word for word in re.split(r"[\s;&|<>]+", command) if word)


def _command_basename(word: str) -> str:
    normalized = word
    if normalized.startswith("$") and len(normalized) > 1:
        normalized = normalized[1:]
    return normalized.rstrip(":").rsplit("/", 1)[-1].lower()


def _has_any(words: tuple[str, ...], candidates: set[str]) -> bool:
    return any(word in candidates for word in words)


def _output_budget(
    request: ShellTestRunSafeSubsetRequest,
    config: ShellTestRunSafeSubsetConfig,
) -> ShellOutputBudget:
    return ShellOutputBudget(
        maxOutputBytes=int(request.max_output_bytes or config.max_output_bytes),
        maxTranscriptBytes=int(request.max_transcript_bytes or config.max_transcript_bytes),
    )


def _receipt_ref(
    *,
    request: ShellTestRunSafeSubsetRequest,
    status: str,
    command_digest: str,
    reason_codes: tuple[str, ...],
    output_budget: ShellOutputBudget,
    policy_decision_ref: str,
) -> str:
    seed = "|".join(
        (
            request.tool_name,
            request.session_id,
            request.workspace_ref,
            request.turn_id,
            status,
            command_digest,
            ",".join(reason_codes),
            policy_decision_ref,
            str(output_budget.max_output_bytes),
            str(output_budget.max_transcript_bytes),
        )
    )
    return "shell-testrun-receipt:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _public_digest(value: str) -> str:
    text = value.strip()
    if _DIGEST_RE.fullmatch(text) and _PRIVATE_TEXT_RE.search(text) is None:
        return text
    return "sha256:" + ("0" * 64)


def _public_receipt_ref(value: str) -> str:
    text = value.strip()
    if _RECEIPT_RE.fullmatch(text) and _PRIVATE_TEXT_RE.search(text) is None:
        return text
    return "redacted_ref"


def _public_policy_ref(value: str) -> str:
    text = value.strip()
    if text and _PRIVATE_REF_RE.search(text) is None:
        return text[:180]
    return "redacted_ref"


def _safe_reason_code(value: object) -> str:
    text = str(value).strip().lower().replace(" ", "_")
    sensitive_markers = (
        "authorization",
        "bearer",
        "cookie",
        "credential",
        "password",
        "secret",
        "token",
        "private_key",
        "session_key",
    )
    if (
        _REASON_CODE_RE.fullmatch(text)
        and _PRIVATE_TEXT_RE.search(text) is None
        and not any(marker in text for marker in sensitive_markers)
    ):
        return text
    return "redacted_reason"


def _false_attachment_flags() -> dict[str, Literal[False]]:
    return {key: False for key in _FALSE_AUTHORITY_OVERRIDES}


def _coerce_authority_flags(value: object) -> ShellTestRunAuthorityFlags:
    if isinstance(value, ShellTestRunAuthorityFlags):
        return value.model_copy(update=_FALSE_AUTHORITY_OVERRIDES)
    if isinstance(value, Mapping):
        data = dict(value)
        data.update(_FALSE_AUTHORITY_OVERRIDES)
        return ShellTestRunAuthorityFlags.model_validate(data)
    return ShellTestRunAuthorityFlags()


__all__ = [
    "CommandClass",
    "SafetyAction",
    "ShellCommandSafetyDecision",
    "ShellOutputBudget",
    "ShellTestRunAuthorityFlags",
    "ShellTestRunDecision",
    "ShellTestRunMaterialization",
    "ShellTestRunSafeSubsetBinding",
    "ShellTestRunSafeSubsetConfig",
    "ShellTestRunSafeSubsetRequest",
    "ShellTestRunStatus",
    "ShellTestRunToolName",
]
