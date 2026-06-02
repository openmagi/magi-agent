from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator


SpreadsheetFormat = Literal["csv", "xlsx"]
SpreadsheetPermissionClass = Literal["read", "write", "meta"]
SpreadsheetBlockedReason = Literal["xlsx_dependency_or_worker_approval_required"] | None

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_OPERATION_IDS = frozenset(
    {
        "spreadsheet.read",
        "spreadsheet.write",
        "spreadsheet.preview",
        "spreadsheet.validate",
        "spreadsheet.reconcile",
        "spreadsheet.xlsx.read",
        "spreadsheet.xlsx.write",
    }
)
_TOOL_NAMES = frozenset(
    {
        "CSVRead",
        "CSVWrite",
        "SpreadsheetPreview",
        "SpreadsheetValidate",
        "SpreadsheetReconcile",
        "XLSXRead",
        "XLSXWrite",
    }
)


class SpreadsheetContractAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_artifact_service_attached: Literal[False] = Field(
        default=False,
        alias="adkArtifactServiceAttached",
    )
    workspace_mutated: Literal[False] = Field(default=False, alias="workspaceMutated")
    channel_delivery_performed: Literal[False] = Field(
        default=False,
        alias="channelDeliveryPerformed",
    )
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

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = update, deep
        return type(self)()


class SpreadsheetOperationContract(BaseModel):
    model_config = _MODEL_CONFIG

    operation_id: str = Field(alias="operationId")
    tool_name: str = Field(alias="toolName")
    format: SpreadsheetFormat
    permission_class: SpreadsheetPermissionClass = Field(alias="permissionClass")
    stdlib_compatible: bool = Field(alias="stdlibCompatible")
    supported: bool
    blocked_reason: SpreadsheetBlockedReason = Field(default=None, alias="blockedReason")
    requires_artifact_ref: bool = Field(default=False, alias="requiresArtifactRef")
    requires_snapshot_ref: bool = Field(default=False, alias="requiresSnapshotRef")
    adk_tool: Mapping[str, object] = Field(alias="adkTool")
    authority_flags: SpreadsheetContractAuthorityFlags = Field(
        default_factory=SpreadsheetContractAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("operation_id")
    @classmethod
    def _validate_operation_id(cls, value: str) -> str:
        if value not in _OPERATION_IDS:
            raise ValueError("unknown spreadsheet operation")
        return value

    @field_validator("tool_name")
    @classmethod
    def _validate_tool_name(cls, value: str) -> str:
        if value not in _TOOL_NAMES:
            raise ValueError("unknown spreadsheet tool name")
        return value

    def public_projection(self) -> dict[str, object]:
        return {
            "operationId": self.operation_id,
            "toolName": self.tool_name,
            "format": self.format,
            "permissionClass": self.permission_class,
            "stdlibCompatible": self.stdlib_compatible,
            "supported": self.supported,
            "blockedReason": self.blocked_reason,
            "requiresArtifactRef": self.requires_artifact_ref,
            "requiresSnapshotRef": self.requires_snapshot_ref,
            "adkTool": dict(self.adk_tool),
            "adkBoundary": {
                "artifactService": "ArtifactService",
                "artifactRefsOnly": True,
            },
            "authorityFlags": self.authority_flags.model_dump(
                by_alias=True,
                mode="json",
            ),
        }


def spreadsheet_contract_catalog() -> tuple[SpreadsheetOperationContract, ...]:
    return _CONTRACTS


def get_spreadsheet_operation_contract(operation_id: str) -> SpreadsheetOperationContract:
    for contract in _CONTRACTS:
        if contract.operation_id == operation_id:
            return contract
    raise KeyError(operation_id)


def _contract(
    operation_id: str,
    tool_name: str,
    *,
    format: SpreadsheetFormat,
    permission_class: SpreadsheetPermissionClass,
    stdlib_compatible: bool,
    supported: bool = True,
    blocked_reason: SpreadsheetBlockedReason = None,
    requires_artifact_ref: bool = False,
    requires_snapshot_ref: bool = False,
) -> SpreadsheetOperationContract:
    return SpreadsheetOperationContract(
        operationId=operation_id,
        toolName=tool_name,
        format=format,
        permissionClass=permission_class,
        stdlibCompatible=stdlib_compatible,
        supported=supported,
        blockedReason=blocked_reason,
        requiresArtifactRef=requires_artifact_ref,
        requiresSnapshotRef=requires_snapshot_ref,
        adkTool=_function_tool_metadata(
            tool_name,
            operation_id=operation_id,
            permission_class=permission_class,
        ),
    )


def _function_tool_metadata(
    tool_name: str,
    *,
    operation_id: str,
    permission_class: SpreadsheetPermissionClass,
) -> dict[str, object]:
    return {
        "name": tool_name,
        "adkToolType": "FunctionTool",
        "enabledByDefault": False,
        "handlerAttached": False,
        "operationId": operation_id,
        "permissionClass": permission_class,
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "artifactRef": {"type": "string"},
                "snapshotRef": {"type": "string"},
                "contentDigest": {"type": "string", "pattern": "^sha256:[a-f0-9]{64}$"},
                "maxPreviewRows": {"type": "integer", "minimum": 1, "maximum": 100},
                "maxPreviewCols": {"type": "integer", "minimum": 1, "maximum": 50},
            },
        },
    }


_XLSX_REASON: Literal["xlsx_dependency_or_worker_approval_required"] = (
    "xlsx_dependency_or_worker_approval_required"
)

_CONTRACTS: tuple[SpreadsheetOperationContract, ...] = (
    _contract(
        "spreadsheet.read",
        "CSVRead",
        format="csv",
        permission_class="read",
        stdlib_compatible=True,
    ),
    _contract(
        "spreadsheet.write",
        "CSVWrite",
        format="csv",
        permission_class="write",
        stdlib_compatible=True,
        requires_artifact_ref=True,
        requires_snapshot_ref=True,
    ),
    _contract(
        "spreadsheet.preview",
        "SpreadsheetPreview",
        format="csv",
        permission_class="meta",
        stdlib_compatible=True,
    ),
    _contract(
        "spreadsheet.validate",
        "SpreadsheetValidate",
        format="csv",
        permission_class="meta",
        stdlib_compatible=True,
    ),
    _contract(
        "spreadsheet.reconcile",
        "SpreadsheetReconcile",
        format="csv",
        permission_class="meta",
        stdlib_compatible=True,
    ),
    _contract(
        "spreadsheet.xlsx.read",
        "XLSXRead",
        format="xlsx",
        permission_class="read",
        stdlib_compatible=False,
        supported=False,
        blocked_reason=_XLSX_REASON,
    ),
    _contract(
        "spreadsheet.xlsx.write",
        "XLSXWrite",
        format="xlsx",
        permission_class="write",
        stdlib_compatible=False,
        supported=False,
        blocked_reason=_XLSX_REASON,
        requires_artifact_ref=True,
        requires_snapshot_ref=True,
    ),
)


__all__ = [
    "SpreadsheetContractAuthorityFlags",
    "SpreadsheetOperationContract",
    "get_spreadsheet_operation_contract",
    "spreadsheet_contract_catalog",
]
