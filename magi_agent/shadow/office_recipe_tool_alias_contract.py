from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


OfficeRecipeToolRef = Literal[
    "tool:spreadsheet.read",
    "tool:spreadsheet.plan-write",
    "tool:browser.inspect",
    "tool:browser.plan-action",
    "tool:document.inspect",
    "tool:script.plan-run",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_REQUIRED_REFS = tuple(OfficeRecipeToolRef.__args__)  # type: ignore[attr-defined]
_REQUIRED_SURFACES_BY_REF: Mapping[OfficeRecipeToolRef, tuple[str, ...]] = {
    "tool:spreadsheet.read": (
        "SpreadsheetWrite",
        "SpreadsheetValidate",
        "SpreadsheetReconcilePreview",
    ),
    "tool:spreadsheet.plan-write": ("SpreadsheetWrite", "FileDeliver", "FileSend"),
    "tool:browser.inspect": ("Browser", "SocialBrowser", "BrowserExtractSnapshot"),
    "tool:browser.plan-action": (
        "Browser",
        "SocialBrowser",
        "BrowserExtractSnapshot",
        "BrowserDownloadReport",
        "BrowserSubmitForm",
    ),
    "tool:document.inspect": (
        "DocumentWrite",
        "DocumentExtractFields",
        "DocumentRedlineSuggest",
        "DocumentDeliverableReview",
    ),
    "tool:script.plan-run": ("LightweightScriptPlan",),
}
_FORBIDDEN_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet)(?:[\\/]|$)|"
    r"pvc|supabase://|s3://|gs://|postgres(?:ql)?://|telegram|canary",
    re.IGNORECASE,
)
_SECRET_SHAPED_VALUE_RE = re.compile(
    r"\b(?:Bearer\s+[A-Za-z0-9._~+/=-]+|gh[opusr]_[A-Za-z0-9_]+|"
    r"sk-[A-Za-z0-9._-]+|[rs]k_(?:live|test)_[A-Za-z0-9_]+)\b|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY)[A-Z0-9_]*\s*[:=]\s*[^,\s}{]{4,}",
    re.IGNORECASE,
)
_FORBIDDEN_RAW_TRUE_KEYS = frozenset(
    {
        "traffic_attached",
        "execution_attached",
        "tool_host_dispatch_attached",
        "adk_runner_invoked",
        "browser_session_attached",
        "external_submit_attached",
        "external_download_attached",
        "artifact_write_attached",
        "artifact_delivery_attached",
        "connector_call_attached",
        "scheduler_runtime_attached",
        "mission_runtime_attached",
        "executable_authority",
        "live_tool_satisfied_by_ts_surface",
        "external_submit_or_download_attached",
        "artifact_write_or_delivery_attached",
        "connector_call_attached",
        "scheduler_or_mission_runtime_attached",
        "imports_adk_primitives",
        "missions_scheduler_modeled_as_long_running_function_tool",
    }
)


class OfficeRecipeToolAliasAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    tool_host_dispatch_attached: Literal[False] = Field(
        default=False,
        alias="toolHostDispatchAttached",
    )
    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    browser_session_attached: Literal[False] = Field(
        default=False,
        alias="browserSessionAttached",
    )
    external_submit_attached: Literal[False] = Field(
        default=False,
        alias="externalSubmitAttached",
    )
    external_download_attached: Literal[False] = Field(
        default=False,
        alias="externalDownloadAttached",
    )
    artifact_write_attached: Literal[False] = Field(
        default=False,
        alias="artifactWriteAttached",
    )
    artifact_delivery_attached: Literal[False] = Field(
        default=False,
        alias="artifactDeliveryAttached",
    )
    connector_call_attached: Literal[False] = Field(
        default=False,
        alias="connectorCallAttached",
    )
    scheduler_runtime_attached: Literal[False] = Field(
        default=False,
        alias="schedulerRuntimeAttached",
    )
    mission_runtime_attached: Literal[False] = Field(
        default=False,
        alias="missionRuntimeAttached",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**{name: False for name in cls.model_fields})

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)

    @field_serializer(
        "traffic_attached",
        "execution_attached",
        "tool_host_dispatch_attached",
        "adk_runner_invoked",
        "browser_session_attached",
        "external_submit_attached",
        "external_download_attached",
        "artifact_write_attached",
        "artifact_delivery_attached",
        "connector_call_attached",
        "scheduler_runtime_attached",
        "mission_runtime_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class OfficeRecipeAdkFirstContract(BaseModel):
    model_config = _MODEL_CONFIG

    recipe_refs_remain_agent_recipe_compiler_metadata: Literal[True] = Field(
        default=True,
        alias="recipeRefsRemainAgentRecipeCompilerMetadata",
    )
    future_atomic_tools_map_to: Literal[
        "ADK FunctionTool through OpenMagi ToolHost policy"
    ] = Field(alias="futureAtomicToolsMapTo")
    future_long_jobs_may_map_to: Literal[
        "LongRunningFunctionTool after approval"
    ] = Field(alias="futureLongJobsMayMapTo")
    missions_scheduler_modeled_as_long_running_function_tool: Literal[False] = Field(
        default=False,
        alias="missionsSchedulerModeledAsLongRunningFunctionTool",
    )
    imports_adk_primitives: Literal[False] = Field(default=False, alias="importsAdkPrimitives")


class OfficeRecipeToolAliasCase(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    abstract_tool_ref: OfficeRecipeToolRef = Field(alias="abstractToolRef")
    recipe_compiler_surface: Literal["AgentRecipeCompiler"] = Field(
        alias="recipeCompilerSurface",
    )
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")
    executable_authority: Literal[False] = Field(default=False, alias="executableAuthority")
    live_tool_satisfied_by_ts_surface: Literal[False] = Field(
        default=False,
        alias="liveToolSatisfiedByTsSurface",
    )
    ts_plugin_surfaces: tuple[str, ...] = Field(alias="tsPluginSurfaces")
    diagnostic_metadata_surfaces: tuple[str, ...] = Field(
        default=(),
        alias="diagnosticMetadataSurfaces",
    )
    external_submit_or_download_intent: bool = Field(
        default=False,
        alias="externalSubmitOrDownloadIntent",
    )
    external_submit_or_download_attached: Literal[False] = Field(
        default=False,
        alias="externalSubmitOrDownloadAttached",
    )
    artifact_write_or_delivery_attached: Literal[False] = Field(
        default=False,
        alias="artifactWriteOrDeliveryAttached",
    )
    connector_call_attached: Literal[False] = Field(
        default=False,
        alias="connectorCallAttached",
    )
    scheduler_or_mission_runtime_attached: Literal[False] = Field(
        default=False,
        alias="schedulerOrMissionRuntimeAttached",
    )
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    attachment_flags: OfficeRecipeToolAliasAttachmentFlags = Field(alias="attachmentFlags")

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_case(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_case(self) -> Self:
        for value in (
            self.case_id,
            self.recipe_compiler_surface,
            *(self.ts_plugin_surfaces),
            *(self.diagnostic_metadata_surfaces),
            *(self.reason_codes),
        ):
            _validate_public_string(value)
        if not self.ts_plugin_surfaces:
            raise ValueError("tool alias case requires TS/plugin metadata surfaces")
        if self.reason_codes[0:1] != ("recipe_ref_metadata_only",):
            raise ValueError("tool alias case must start with recipe_ref_metadata_only")
        if self.ts_plugin_surfaces != _REQUIRED_SURFACES_BY_REF[self.abstract_tool_ref]:
            raise ValueError(
                f"{self.abstract_tool_ref} must record represented adjacent surfaces"
            )
        if self.abstract_tool_ref == "tool:spreadsheet.read":
            if (
                "spreadsheet_write_does_not_satisfy_live_read_validate_reconcile"
                not in self.reason_codes
            ):
                raise ValueError("spreadsheet.read must not imply SpreadsheetWrite live authority")
        return self


class OfficeRecipeToolAliasFixture(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    version: int
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    adk_first_contract: OfficeRecipeAdkFirstContract = Field(alias="adkFirstContract")
    attachment_flags: OfficeRecipeToolAliasAttachmentFlags = Field(alias="attachmentFlags")
    cases: tuple[OfficeRecipeToolAliasCase, ...]

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_fixture(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_fixture(self) -> Self:
        if self.version < 1:
            raise ValueError("tool alias fixture version must be positive")
        refs = tuple(case.abstract_tool_ref for case in self.cases)
        if refs != _REQUIRED_REFS:
            raise ValueError("tool alias fixture must cover required refs in order")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("tool alias fixture caseIds must be unique")
        return self


class OfficeRecipeToolAliasProjection(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    local_diagnostic: bool = Field(alias="localDiagnostic")
    case_order: tuple[str, ...] = Field(alias="caseOrder")
    attachment_flags: OfficeRecipeToolAliasAttachmentFlags = Field(alias="attachmentFlags")
    no_live_execution: bool = Field(alias="noLiveExecution")
    abstract_tool_refs: tuple[str, ...] = Field(alias="abstractToolRefs")
    ts_plugin_surfaces_by_ref: dict[str, tuple[str, ...]] = Field(
        alias="tsPluginSurfacesByRef",
    )
    adk_first_contract: dict[str, object] = Field(alias="adkFirstContract")


def load_office_recipe_tool_alias_fixture(
    filename: str,
    *,
    fixture_root: Path,
) -> OfficeRecipeToolAliasFixture:
    path = (fixture_root / filename).resolve()
    root = fixture_root.resolve()
    if root not in path.parents:
        raise ValueError("tool alias fixture path must stay under fixture_root")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return OfficeRecipeToolAliasFixture.model_validate(payload)


def project_office_recipe_tool_alias_fixture(
    fixture: OfficeRecipeToolAliasFixture | Mapping[str, Any],
) -> OfficeRecipeToolAliasProjection:
    if not isinstance(fixture, OfficeRecipeToolAliasFixture):
        fixture = OfficeRecipeToolAliasFixture.model_validate(fixture)
    return OfficeRecipeToolAliasProjection(
        fixtureId=fixture.fixture_id,
        localDiagnostic=fixture.local_diagnostic,
        caseOrder=tuple(case.case_id for case in fixture.cases),
        attachmentFlags=fixture.attachment_flags,
        noLiveExecution=True,
        abstractToolRefs=tuple(case.abstract_tool_ref for case in fixture.cases),
        tsPluginSurfacesByRef={
            case.abstract_tool_ref: case.ts_plugin_surfaces for case in fixture.cases
        },
        adkFirstContract=fixture.adk_first_contract.model_dump(by_alias=True),
    )


def _reject_unsafe_raw_value(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = _camel_to_snake(str(key))
            if normalized_key in _FORBIDDEN_RAW_TRUE_KEYS and item is True:
                raise ValueError("tool alias fixture cannot attach runtime authority")
            _reject_unsafe_raw_value(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_unsafe_raw_value(item)
    elif isinstance(value, str):
        _validate_public_string(value)


def _validate_public_string(value: str) -> None:
    if not value.strip():
        raise ValueError("tool alias fixture metadata fields must be non-empty")
    if _FORBIDDEN_PATH_RE.search(value) or _SECRET_SHAPED_VALUE_RE.search(value):
        raise ValueError("tool alias fixture contains unsafe production metadata")


def _camel_to_snake(value: str) -> str:
    chars: list[str] = []
    for index, char in enumerate(value):
        if char.isupper() and index:
            chars.append("_")
        chars.append(char.lower())
    return "".join(chars)


__all__ = [
    "OfficeRecipeAdkFirstContract",
    "OfficeRecipeToolAliasAttachmentFlags",
    "OfficeRecipeToolAliasCase",
    "OfficeRecipeToolAliasFixture",
    "OfficeRecipeToolAliasProjection",
    "load_office_recipe_tool_alias_fixture",
    "project_office_recipe_tool_alias_fixture",
]
