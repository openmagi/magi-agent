from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


ProjectionWriteTarget: TypeAlias = Literal[
    "transcript",
    "sse",
    "control_event",
    "control_request",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)


class ProjectionWriteAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    transcript_write_allowed: Literal[False] = Field(
        default=False,
        alias="transcriptWriteAllowed",
    )
    sse_write_allowed: Literal[False] = Field(default=False, alias="sseWriteAllowed")
    control_event_write_allowed: Literal[False] = Field(
        default=False,
        alias="controlEventWriteAllowed",
    )
    control_request_write_allowed: Literal[False] = Field(
        default=False,
        alias="controlRequestWriteAllowed",
    )
    durable_write_allowed: Literal[False] = Field(
        default=False,
        alias="durableWriteAllowed",
    )
    production_receipt_allowed: Literal[False] = Field(
        default=False,
        alias="productionReceiptAllowed",
    )
    storage_backend_attached: Literal[False] = Field(
        default=False,
        alias="storageBackendAttached",
    )
    filesystem_write_allowed: Literal[False] = Field(
        default=False,
        alias="filesystemWriteAllowed",
    )
    database_write_allowed: Literal[False] = Field(
        default=False,
        alias="databaseWriteAllowed",
    )
    transport_write_allowed: Literal[False] = Field(
        default=False,
        alias="transportWriteAllowed",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**_false_authority_payload(cls))

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        return type(self)(**_false_authority_payload(type(self)))

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
            raise ValueError("projection write authority flags contain unsupported fields")
        return _false_authority_payload(cls)

    @field_serializer(
        "transcript_write_allowed",
        "sse_write_allowed",
        "control_event_write_allowed",
        "control_request_write_allowed",
        "durable_write_allowed",
        "production_receipt_allowed",
        "storage_backend_attached",
        "filesystem_write_allowed",
        "database_write_allowed",
        "transport_write_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class ProjectionWriteIntent(BaseModel):
    model_config = _MODEL_CONFIG

    target: ProjectionWriteTarget
    operation: str
    session_key: str = Field(alias="sessionKey")
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")
    payload: dict[str, object] = Field(default_factory=dict)


class ProjectionWriteDenial(BaseModel):
    model_config = _MODEL_CONFIG

    target: ProjectionWriteTarget
    operation: str
    reason_code: Literal["projection_writes_disabled"] = Field(alias="reasonCode")
    durable_write_attempted: Literal[False] = Field(
        default=False,
        alias="durableWriteAttempted",
    )
    production_receipt_produced: Literal[False] = Field(
        default=False,
        alias="productionReceiptProduced",
    )
    message: str


class ProjectionWriteReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    receipt_id: str = Field(alias="receiptId")
    storage_backend: str = Field(alias="storageBackend")
    target: ProjectionWriteTarget
    rollback_supported: bool = Field(alias="rollbackSupported")
    support_reference: str = Field(alias="supportReference")
    retention_policy: str = Field(alias="retentionPolicy")
    checksum: str
    timestamp: int | float


class ProjectionWriteBoundaryResult(BaseModel):
    model_config = _MODEL_CONFIG

    allowed: Literal[False]
    target: ProjectionWriteTarget
    operation: str
    durable_write_attempted: Literal[False] = Field(alias="durableWriteAttempted")
    production_receipt_produced: Literal[False] = Field(
        alias="productionReceiptProduced",
    )
    authority_flags: ProjectionWriteAuthorityFlags = Field(alias="authorityFlags")
    denial: ProjectionWriteDenial
    receipt: ProjectionWriteReceipt | None


def evaluate_projection_write_intent(
    intent: ProjectionWriteIntent | Mapping[str, Any],
) -> ProjectionWriteBoundaryResult:
    safe_intent = ProjectionWriteIntent.model_validate(intent)
    denial = ProjectionWriteDenial(
        target=safe_intent.target,
        operation=safe_intent.operation,
        reasonCode="projection_writes_disabled",
        message=(
            "Projection compatibility writes are disabled until an explicit "
            "storage backend and receipt policy are attached."
        ),
    )
    return ProjectionWriteBoundaryResult(
        allowed=False,
        target=safe_intent.target,
        operation=safe_intent.operation,
        durableWriteAttempted=False,
        productionReceiptProduced=False,
        authorityFlags=ProjectionWriteAuthorityFlags(),
        denial=denial,
        receipt=None,
    )


def evaluate_coding_final_projection_write(
    intent: ProjectionWriteIntent | Mapping[str, Any],
) -> ProjectionWriteBoundaryResult:
    """Evaluate a write intent for coding final projection events.

    Coding final projections are read-only summaries of verified evidence.
    They never require write authority — this function always denies the
    write and confirms that no production mutation occurred.
    """
    return evaluate_projection_write_intent(intent)


def _false_authority_payload(
    model_type: type[ProjectionWriteAuthorityFlags],
) -> dict[str, bool]:
    return {
        field.alias or name: False
        for name, field in model_type.model_fields.items()
    }
