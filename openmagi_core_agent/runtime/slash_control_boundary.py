from __future__ import annotations

import hashlib
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer


SlashControlStatus = Literal["disabled", "blocked", "command_intent"]
SlashCommand = Literal[
    "compact",
    "reset",
    "status",
    "onboarding",
    "plan",
    "goal",
    "superpowers",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|COOKIE)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users/[^,\s\"']+|/workspace/[^,\s\"']+|/data/bots/[^,\s\"']+|"
    r"/var/lib/kubelet/[^,\s\"']+|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)
_RAW_PRIVATE_LINE_RE = re.compile(
    r"raw[_ -]?(?:transcript|tool|prompt|output|result|log|args)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|private[_ -]?memory|"
    r"authorization|cookie|set-cookie",
    re.IGNORECASE,
)
_COMMAND_RE = re.compile(r"^/([A-Za-z0-9:_-]+)(?:\s+(.*))?$", re.DOTALL)


class SlashControlConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_command_projection_enabled: bool = Field(
        default=False,
        alias="localFakeCommandProjectionEnabled",
    )
    slash_runtime_attached: Literal[False] = Field(default=False, alias="slashRuntimeAttached")
    production_writes_enabled: Literal[False] = Field(default=False, alias="productionWritesEnabled")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")


class SlashControlAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    slash_runtime_attached: Literal[False] = Field(default=False, alias="slashRuntimeAttached")
    plan_mode_mutated: Literal[False] = Field(default=False, alias="planModeMutated")
    session_reset_performed: Literal[False] = Field(default=False, alias="sessionResetPerformed")
    compaction_performed: Literal[False] = Field(default=False, alias="compactionPerformed")
    production_writes_enabled: Literal[False] = Field(default=False, alias="productionWritesEnabled")
    user_visible_output: Literal[False] = Field(default=False, alias="userVisibleOutput")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(self, *, update: dict[str, Any] | None = None, deep: bool = False) -> Self:
        _ = update, deep
        return type(self)()

    @field_serializer(
        "slash_runtime_attached",
        "plan_mode_mutated",
        "session_reset_performed",
        "compaction_performed",
        "production_writes_enabled",
        "user_visible_output",
        "route_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class SlashControlRequest(BaseModel):
    model_config = _MODEL_CONFIG

    text: str
    session_key: str = Field(alias="sessionKey")
    turn_id: str | None = Field(default=None, alias="turnId")
    metadata: dict[str, object] = Field(default_factory=dict)


class SlashCommandIntent(BaseModel):
    model_config = _MODEL_CONFIG

    command: SlashCommand
    raw_command: str = Field(alias="rawCommand")
    argument_preview: str = Field(default="", alias="argumentPreview")
    control_ref: str = Field(alias="controlRef")
    recipe_pack_ref: str | None = Field(default=None, alias="recipePackRef")
    checkpoint_ref: str | None = Field(default=None, alias="checkpointRef")

    def public_projection(self) -> dict[str, object]:
        return {
            "command": self.command,
            "rawCommand": _safe_text(self.raw_command)[:80],
            "argumentPreview": _safe_text(self.argument_preview)[:240],
            "controlRef": _public_ref(self.control_ref, "control"),
            "recipePackRef": (
                None if self.recipe_pack_ref is None else _public_ref(self.recipe_pack_ref, "recipe")
            ),
            "checkpointRef": (
                None if self.checkpoint_ref is None else _public_ref(self.checkpoint_ref, "checkpoint")
            ),
        }


class SlashControlDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: SlashControlStatus
    intent: SlashCommandIntent | None = None
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    diagnostic_metadata: dict[str, object] = Field(default_factory=dict, alias="diagnosticMetadata")
    authority_flags: SlashControlAuthorityFlags = Field(
        default_factory=SlashControlAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set
        values["authorityFlags"] = SlashControlAuthorityFlags()
        return cls.model_validate(values)

    def model_copy(self, *, update: dict[str, Any] | None = None, deep: bool = False) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(update)
        data["authorityFlags"] = SlashControlAuthorityFlags()
        return type(self).model_validate(data)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "intent": None if self.intent is None else self.intent.public_projection(),
            "reasonCodes": list(self.reason_codes),
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class SlashControlBoundary:
    """Default-off slash/control command projection boundary.

    This parser produces local command intents only. It does not mutate plan
    mode, reset sessions, compact context, invoke Superpowers, or write control
    events/routes.
    """

    def __init__(self, config: SlashControlConfig) -> None:
        self.config = config

    def project(self, request: SlashControlRequest) -> SlashControlDecision:
        diagnostics = {
            "enabled": self.config.enabled,
            "localFakeCommandProjectionEnabled": self.config.local_fake_command_projection_enabled,
            "slashRuntimeAttached": False,
            "productionWritesEnabled": False,
            "routeAttached": False,
            **request.metadata,
        }
        if not self.config.enabled:
            return _decision("disabled", ("slash_control_disabled",), diagnostics)
        if not self.config.local_fake_command_projection_enabled:
            return _decision("disabled", ("local_slash_projection_disabled",), diagnostics)
        parsed = _parse_command(request.text)
        if parsed is None:
            return _decision("blocked", ("not_a_supported_slash_command",), diagnostics)
        command, raw_command, raw_argument = parsed
        if _contains_raw_private(raw_argument):
            return _decision("blocked", ("slash_argument_private_payload_blocked",), diagnostics)
        argument = _safe_text(raw_argument)
        intent = SlashCommandIntent(
            command=command,
            rawCommand=raw_command,
            argumentPreview=argument,
            controlRef=_control_ref(request, command),
            recipePackRef=_recipe_pack_ref(command),
            checkpointRef=_checkpoint_ref(command),
        )
        return _decision(
            "command_intent",
            (f"slash_{command}_intent_only",),
            diagnostics,
            intent=intent,
        )


def _decision(
    status: SlashControlStatus,
    reason_codes: tuple[str, ...],
    diagnostics: dict[str, object],
    *,
    intent: SlashCommandIntent | None = None,
) -> SlashControlDecision:
    return SlashControlDecision(
        status=status,
        intent=intent,
        reasonCodes=reason_codes,
        diagnosticMetadata=_safe_metadata(diagnostics),
        authorityFlags=SlashControlAuthorityFlags(),
    )


def _parse_command(text: str) -> tuple[SlashCommand, str, str] | None:
    match = _COMMAND_RE.match(text.strip())
    if match is None:
        return None
    raw = match.group(1)
    args = match.group(2) or ""
    normalized = raw.casefold()
    if normalized.startswith("superpowers:"):
        return "superpowers", raw, args
    aliases: dict[str, SlashCommand] = {
        "compact": "compact",
        "reset": "reset",
        "status": "status",
        "onboarding": "onboarding",
        "plan": "plan",
        "goal": "goal",
    }
    command = aliases.get(normalized)
    if command is None:
        return None
    return command, raw, args


def _recipe_pack_ref(command: SlashCommand) -> str | None:
    if command in {"plan", "goal", "onboarding", "superpowers"}:
        return "openmagi.agent-methodology"
    return None


def _checkpoint_ref(command: SlashCommand) -> str | None:
    if command in {"plan", "goal", "onboarding", "superpowers"}:
        return f"checkpoint:agent-methodology:{command}"
    if command == "compact":
        return "checkpoint:session:compaction-intent"
    if command == "reset":
        return "checkpoint:session:reset-intent"
    return None


def _control_ref(request: SlashControlRequest, command: SlashCommand) -> str:
    seed = f"{request.session_key}:{request.turn_id or ''}:{command}:{request.text}"
    return f"control:{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"


def _contains_raw_private(value: str) -> bool:
    return bool(_RAW_PRIVATE_LINE_RE.search(value) or _PRIVATE_PATH_RE.search(value))


def _public_ref(value: str, prefix: str) -> str:
    clean = _safe_text(value)
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{1,180}", clean):
        return clean
    return f"{prefix}:{hashlib.sha1(str(value).encode('utf-8')).hexdigest()[:16]}"


def _safe_metadata(metadata: dict[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
        if any(marker in normalized_key for marker in ("raw", "token", "secret", "cookie", "path", "prompt")):
            continue
        if isinstance(value, str):
            clean = _safe_text(value)
            if clean:
                safe[str(key)] = clean[:240]
        elif isinstance(value, bool | int | float) or value is None:
            safe[str(key)] = value
    return safe


def _safe_text(value: str) -> str:
    safe_lines = [
        line
        for line in value.splitlines()
        if _RAW_PRIVATE_LINE_RE.search(line) is None and _PRIVATE_PATH_RE.search(line) is None
    ]
    clean = "\n".join(safe_lines)
    clean = _SECRET_TEXT_RE.sub("[redacted]", clean)
    clean = _PRIVATE_PATH_RE.sub("[redacted-path]", clean)
    return clean.strip()


__all__ = [
    "SlashCommandIntent",
    "SlashControlAuthorityFlags",
    "SlashControlBoundary",
    "SlashControlConfig",
    "SlashControlDecision",
    "SlashControlRequest",
]
