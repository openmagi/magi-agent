from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


RequestLedgerStatus: TypeAlias = Literal["skipped", "recorded"]
RequestLedgerReason: TypeAlias = Literal["disabled", "local_request_shape_recorded"]
RequestLedgerStage: TypeAlias = Literal[
    "model_input",
    "tool_request",
    "runtime_control",
    "validator_checkpoint",
    "evidence_attach",
]
RuntimeControlRecipePolicy: TypeAlias = Literal[
    "fail_closed",
    "fail_open_to_typescript",
    "audit_only",
]
RuntimeControlValidatorStatus: TypeAlias = Literal[
    "passed",
    "blocked",
    "repair_required",
]
RuntimeControlApprovalStatus: TypeAlias = Literal[
    "not_required",
    "pending",
    "approved",
    "denied",
    "timed_out",
]
RuntimeControlAction: TypeAlias = Literal[
    "continue",
    "await_approval",
    "block",
    "restore_typescript",
    "audit",
    "repair",
]
ApprovalGateStatus: TypeAlias = Literal[
    "pending",
    "approved",
    "denied",
    "timed_out",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_REF_RE = re.compile(
    r"^[a-z][a-z0-9+.-]*://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=-]{0,511}$"
)
_SCOPED_REF_RE = re.compile(
    r"^[a-z][a-z0-9+.-]*:"
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
    r"(?::[A-Za-z0-9][A-Za-z0-9._-]{0,127}){0,15}$"
)
_NESTED_URI_RE = re.compile(r"[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_PRIVATE_RE = re.compile(
    r"(?:"
    r"authorization\s*:|"
    r"\bbearer\b|"
    r"\bcookie\b|"
    r"\bcredential\b|"
    r"\bsession[_-]?key\b|"
    r"\bapi[_-]?key\b|"
    r"\bsecret\b|"
    r"\bpassword\b|"
    r"\btoken\b|"
    r"\bsk-[A-Za-z0-9_-]+|"
    r"gh[opusr]_|"
    r"github_pat_|"
    r"xox[a-z]-|"
    r"AIza|"
    r"/workspace(?:/|\b)|"
    r"/data/bots(?:/|\b)|"
    r"/Users(?:/|\b)|"
    r"/home(?:/|\b)|"
    r"/var/lib/kubelet(?:/|\b)|"
    r"hidden[ _-]?reasoning|"
    r"chain[ _-]?of[ _-]?thought|"
    r"child[ _-]?prompt|"
    r"raw[ _-]?tool|"
    r"tool[ _-]?log|"
    r"raw[ _-]?result|"
    r"private[ _-]?memory|"
    r"telegram"
    r")",
    re.IGNORECASE,
)
_SAFE_REF_SCHEMES: dict[str, frozenset[str]] = {
    "model_input": frozenset({"session", "summary", "memory-ref"}),
    "tool": frozenset({"tool"}),
    "control": frozenset({"control"}),
    "validator": frozenset({"validator"}),
    "checkpoint": frozenset({"checkpoint"}),
    "evidence": frozenset({"evidence"}),
}
_SAFE_PUBLIC_KEYS = frozenset(
    {
        "boundaryId",
        "droppedEvents",
        "eventCount",
        "maxContextEvents",
        "maxImportedEvents",
        "policy",
        "reasonCode",
        "status",
        "summaryRef",
        "truncated",
    }
)


class _RequestLedgerModel(BaseModel):
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


class RequestLedgerConfig(_RequestLedgerModel):
    enabled: bool = False
    max_entries: int = Field(default=256, ge=1, le=4096, alias="maxEntries")


class RequestLedgerAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    model_context_write_allowed: Literal[False] = Field(
        default=False,
        alias="modelContextWriteAllowed",
    )
    tool_dispatch_allowed: Literal[False] = Field(
        default=False,
        alias="toolDispatchAllowed",
    )
    production_write_allowed: Literal[False] = Field(
        default=False,
        alias="productionWriteAllowed",
    )
    route_activation_allowed: Literal[False] = Field(
        default=False,
        alias="routeActivationAllowed",
    )
    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )
    memory_provider_call_allowed: Literal[False] = Field(
        default=False,
        alias="memoryProviderCallAllowed",
    )
    workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="workspaceMutationAllowed",
    )
    child_execution_allowed: Literal[False] = Field(
        default=False,
        alias="childExecutionAllowed",
    )
    channel_write_allowed: Literal[False] = Field(
        default=False,
        alias="channelWriteAllowed",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**_false_flag_payload(cls))

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        return type(self)(**_false_flag_payload(type(self)))

    @model_validator(mode="before")
    @classmethod
    def _force_false_inputs(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        allowed_keys = set(cls.model_fields)
        allowed_keys.update(
            field.alias
            for field in cls.model_fields.values()
            if field.alias is not None
        )
        unsupported = set(value) - allowed_keys
        if unsupported:
            raise ValueError("request ledger authority flags contain unsupported fields")
        return _false_flag_payload(cls)

    @field_serializer(
        "model_context_write_allowed",
        "tool_dispatch_allowed",
        "production_write_allowed",
        "route_activation_allowed",
        "user_visible_output_allowed",
        "memory_provider_call_allowed",
        "workspace_mutation_allowed",
        "child_execution_allowed",
        "channel_write_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class RequestLedgerDiagnostics(_RequestLedgerModel):
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    rejected_ref_count: int = Field(default=0, ge=0, alias="rejectedRefCount")
    redaction_count: int = Field(default=0, ge=0, alias="redactionCount")


class RequestShapeLedgerEntry(_RequestLedgerModel):
    turn_id: str = Field(alias="turnId")
    stage: RequestLedgerStage
    model_input_refs: tuple[str, ...] = Field(default=(), alias="modelInputRefs")
    tool_refs: tuple[str, ...] = Field(default=(), alias="toolRefs")
    control_refs: tuple[str, ...] = Field(default=(), alias="controlRefs")
    validator_refs: tuple[str, ...] = Field(default=(), alias="validatorRefs")
    checkpoint_refs: tuple[str, ...] = Field(default=(), alias="checkpointRefs")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    budget: Mapping[str, object] = Field(default_factory=dict)
    compaction: Mapping[str, object] = Field(default_factory=dict)
    public_preview: Mapping[str, object] = Field(default_factory=dict, alias="publicPreview")
    raw_payload: object | None = Field(default=None, alias="rawPayload")
    redaction_count: int = Field(default=0, ge=0, alias="redactionCount")

    @field_serializer("turn_id")
    def _serialize_safe_turn_id(self, value: str) -> str:
        return _safe_public_label(value, prefix="turn")


class RequestShapeLedgerResult(_RequestLedgerModel):
    status: RequestLedgerStatus
    reason: RequestLedgerReason
    recorded: bool
    entry: RequestShapeLedgerEntry | None = None
    diagnostics: RequestLedgerDiagnostics = Field(
        default_factory=RequestLedgerDiagnostics,
    )
    authority_flags: RequestLedgerAuthorityFlags = Field(
        default_factory=RequestLedgerAuthorityFlags,
        alias="authorityFlags",
    )


class RequestShapeLedger:
    def __init__(self) -> None:
        self._entries: list[RequestShapeLedgerEntry] = []

    @property
    def entries(self) -> tuple[RequestShapeLedgerEntry, ...]:
        return tuple(self._entries)

    def record(
        self,
        entry: RequestShapeLedgerEntry | Mapping[str, object],
        *,
        config: RequestLedgerConfig | Mapping[str, object] | None = None,
    ) -> RequestShapeLedgerResult:
        safe_config = RequestLedgerConfig.model_validate(config or {})
        safe_entry = RequestShapeLedgerEntry.model_validate(entry)

        if not safe_config.enabled:
            return RequestShapeLedgerResult(
                status="skipped",
                reason="disabled",
                recorded=False,
                entry=None,
                diagnostics=RequestLedgerDiagnostics(reasonCodes=("ledger_disabled",)),
                authorityFlags=RequestLedgerAuthorityFlags(),
            )

        sanitized_entry, diagnostics = _sanitize_entry(safe_entry)
        self._entries.append(sanitized_entry)
        if len(self._entries) > safe_config.max_entries:
            self._entries = self._entries[-safe_config.max_entries :]

        return RequestShapeLedgerResult(
            status="recorded",
            reason="local_request_shape_recorded",
            recorded=True,
            entry=sanitized_entry,
            diagnostics=diagnostics,
            authorityFlags=RequestLedgerAuthorityFlags(),
        )


class RuntimeControlDecision(_RequestLedgerModel):
    action: RuntimeControlAction
    recipe_policy: RuntimeControlRecipePolicy = Field(alias="recipePolicy")
    validator_status: RuntimeControlValidatorStatus = Field(alias="validatorStatus")
    approval_status: RuntimeControlApprovalStatus = Field(alias="approvalStatus")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    blocking: bool
    restore_typescript: bool = Field(alias="restoreTypescript")
    authority_flags: RequestLedgerAuthorityFlags = Field(
        default_factory=RequestLedgerAuthorityFlags,
        alias="authorityFlags",
    )


class ApprovalGateResult(_RequestLedgerModel):
    request_id: str = Field(alias="requestId")
    status: ApprovalGateStatus
    control_refs: tuple[str, ...] = Field(default=(), alias="controlRefs")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    execute_allowed: Literal[False] = Field(default=False, alias="executeAllowed")
    authority_flags: RequestLedgerAuthorityFlags = Field(
        default_factory=RequestLedgerAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _sanitize_approval_payload(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        request_id = data.get("requestId", data.get("request_id"))
        for field_name in (
            "request_id",
            "control_refs",
            "evidence_refs",
            "execute_allowed",
            "authority_flags",
        ):
            data.pop(field_name, None)
        data["requestId"] = (
            _safe_control_request_id(request_id)
            if isinstance(request_id, str)
            else "control:redacted"
        )
        control_refs = data.get("controlRefs", data.get("control_refs", ()))
        if isinstance(control_refs, Sequence) and not isinstance(
            control_refs,
            str | bytes | bytearray,
        ):
            data["controlRefs"] = _filter_refs(tuple(str(ref) for ref in control_refs), "control")[0]
        else:
            data["controlRefs"] = ()
        evidence_refs = data.get("evidenceRefs", data.get("evidence_refs", ()))
        if isinstance(evidence_refs, Sequence) and not isinstance(
            evidence_refs,
            str | bytes | bytearray,
        ):
            data["evidenceRefs"] = _filter_refs(tuple(str(ref) for ref in evidence_refs), "evidence")[0]
        else:
            data["evidenceRefs"] = ()
        data["executeAllowed"] = False
        data["authorityFlags"] = RequestLedgerAuthorityFlags()
        return data

    def resolve(
        self,
        *,
        decision: Literal["approved", "denied"],
        evidenceRefs: Sequence[str] = (),
    ) -> ApprovalGateResult:
        safe_refs, _codes = _filter_refs(evidenceRefs, "evidence")
        return type(self)(
            requestId=self.request_id,
            status=decision,
            controlRefs=self.control_refs,
            evidenceRefs=tuple(safe_refs),
            executeAllowed=False,
            authorityFlags=RequestLedgerAuthorityFlags(),
        )

    def timeout(self) -> ApprovalGateResult:
        return type(self)(
            requestId=self.request_id,
            status="timed_out",
            controlRefs=self.control_refs,
            evidenceRefs=self.evidence_refs,
            executeAllowed=False,
            authorityFlags=RequestLedgerAuthorityFlags(),
        )


def build_runtime_control_decision(
    *,
    recipePolicy: RuntimeControlRecipePolicy,
    validatorStatus: RuntimeControlValidatorStatus,
    approvalStatus: RuntimeControlApprovalStatus,
    evidenceRefs: Sequence[str] = (),
) -> RuntimeControlDecision:
    evidence_refs, _codes = _filter_refs(evidenceRefs, "evidence")
    action: RuntimeControlAction
    blocking = False
    restore_typescript = False

    if approvalStatus == "pending":
        action = "await_approval"
        blocking = True
    elif approvalStatus in {"denied", "timed_out"}:
        action = "block"
        blocking = True
    elif validatorStatus == "repair_required":
        action = "repair"
        blocking = True
    elif validatorStatus == "blocked":
        blocking = True
        if recipePolicy == "fail_open_to_typescript":
            action = "restore_typescript"
            restore_typescript = True
        elif recipePolicy == "audit_only":
            action = "audit"
            blocking = False
        else:
            action = "block"
    else:
        action = "continue"

    return RuntimeControlDecision(
        action=action,
        recipePolicy=recipePolicy,
        validatorStatus=validatorStatus,
        approvalStatus=approvalStatus,
        evidenceRefs=tuple(evidence_refs),
        blocking=blocking,
        restoreTypescript=restore_typescript,
        authorityFlags=RequestLedgerAuthorityFlags(),
    )


def _sanitize_entry(
    entry: RequestShapeLedgerEntry,
) -> tuple[RequestShapeLedgerEntry, RequestLedgerDiagnostics]:
    model_refs, model_codes = _filter_refs(entry.model_input_refs, "model_input")
    tool_refs, tool_codes = _filter_refs(entry.tool_refs, "tool")
    control_refs, control_codes = _filter_refs(entry.control_refs, "control")
    validator_refs, validator_codes = _filter_refs(entry.validator_refs, "validator")
    checkpoint_refs, checkpoint_codes = _filter_refs(entry.checkpoint_refs, "checkpoint")
    evidence_refs, evidence_codes = _filter_refs(entry.evidence_refs, "evidence")
    budget = _sanitize_public_mapping(entry.budget)
    compaction = _sanitize_public_mapping(entry.compaction)
    public_preview: dict[str, object] = dict(_sanitize_public_mapping(entry.public_preview))
    redaction_count = entry.redaction_count

    if budget:
        public_preview["budget"] = budget
    if compaction:
        public_preview["compaction"] = compaction
    if entry.raw_payload is not None:
        public_preview["rawPayload"] = "[redacted]"
        redaction_count += 1

    reason_codes = tuple(
        dict.fromkeys(
            (
                *model_codes,
                *tool_codes,
                *control_codes,
                *validator_codes,
                *checkpoint_codes,
                *evidence_codes,
            )
        )
    )
    diagnostics = RequestLedgerDiagnostics(
        reasonCodes=reason_codes,
        rejectedRefCount=sum(
            1
            for codes in (
                model_codes,
                tool_codes,
                control_codes,
                validator_codes,
                checkpoint_codes,
                evidence_codes,
            )
            for _code in codes
        ),
        redactionCount=redaction_count,
    )

    return (
        RequestShapeLedgerEntry(
            turnId=_safe_public_label(entry.turn_id, prefix="turn"),
            stage=entry.stage,
            modelInputRefs=tuple(model_refs),
            toolRefs=tuple(tool_refs),
            controlRefs=tuple(control_refs),
            validatorRefs=tuple(validator_refs),
            checkpointRefs=tuple(checkpoint_refs),
            evidenceRefs=tuple(evidence_refs),
            budget=budget,
            compaction=compaction,
            publicPreview=public_preview,
            rawPayload=None,
            redactionCount=redaction_count,
        ),
        diagnostics,
    )


def _filter_refs(
    refs: Sequence[str],
    category: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    safe: list[str] = []
    codes: list[str] = []
    for ref in refs:
        if _is_safe_ref(ref, category):
            safe.append(ref)
        else:
            codes.append(f"unsafe_{category}_ref_rejected")
    return tuple(safe), tuple(codes)


def _is_safe_ref(ref: str, category: str) -> bool:
    if not isinstance(ref, str) or not ref:
        return False
    if _PRIVATE_RE.search(ref):
        return False
    if len(ref) > 512:
        return False
    if len(_NESTED_URI_RE.findall(ref)) > 1:
        return False

    allowed_schemes = _SAFE_REF_SCHEMES[category]
    if _REF_RE.fullmatch(ref):
        scheme = ref.split("://", 1)[0]
        return scheme in allowed_schemes
    if _SCOPED_REF_RE.fullmatch(ref):
        scheme = ref.split(":", 1)[0]
        return scheme in allowed_schemes
    return False


def _sanitize_public_mapping(value: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, raw in value.items():
        if key not in _SAFE_PUBLIC_KEYS:
            continue
        sanitized = _sanitize_public_value(raw)
        if sanitized is not None:
            safe[key] = sanitized
    return safe


def _sanitize_public_value(value: object) -> object | None:
    if isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        if len(value) > 256 or _PRIVATE_RE.search(value):
            return None
        return value
    if isinstance(value, Mapping):
        return _sanitize_public_mapping(value)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        safe_items = tuple(
            item for item in (_sanitize_public_value(item) for item in value) if item is not None
        )
        return safe_items
    return None


def _safe_public_label(value: str, *, prefix: str) -> str:
    if _PRIVATE_RE.search(value) or len(value) > 180:
        return f"{prefix}:redacted"
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,179}", value):
        return value
    return f"{prefix}:redacted"


def _safe_control_request_id(value: str) -> str:
    if (
        _PRIVATE_RE.search(value)
        or value.startswith("tool-permission:")
        or len(value) > 180
    ):
        return _hashed_public_ref(value, prefix="control")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,179}", value):
        return value
    if _is_safe_ref(value, "control"):
        return value
    return _hashed_public_ref(value, prefix="control")


def _hashed_public_ref(value: str, *, prefix: str) -> str:
    return f"{prefix}:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def _false_flag_payload(model_type: type[RequestLedgerAuthorityFlags]) -> dict[str, bool]:
    return {
        field.alias or name: False
        for name, field in model_type.model_fields.items()
    }
