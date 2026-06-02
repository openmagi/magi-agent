from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from openmagi_core_agent.recipes.first_party.general_automation.presets import (
    BrowserAction,
    GENERAL_AUTOMATION_PRESET_IDS,
    GeneralAutomationPreset,
    PermissionName,
    general_automation_preset_catalog,
    get_general_automation_preset,
)


MUTATION_TOOL_CATEGORIES = frozenset(
    {
        "artifact_evidence",
        "browser_click",
        "browser_download",
        "browser_fill",
        "browser_submit",
        "delivery_receipt_required",
        "external_directory_policy",
        "workspace_write_policy",
    }
)
_BROWSER_ESCALATION_ACTIONS = frozenset({"click", "fill", "download", "submit"})
_BROWSER_ESCALATION_CATEGORIES = frozenset(
    {"browser_click", "browser_fill", "browser_download", "browser_submit"}
)
_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)


class GeneralAutomationPresetAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    runner_spawned: Literal[False] = Field(default=False, alias="runnerSpawned")
    live_tools_attached: Literal[False] = Field(default=False, alias="liveToolsAttached")
    browser_session_started: Literal[False] = Field(default=False, alias="browserSessionStarted")
    production_route_attached: Literal[False] = Field(
        default=False,
        alias="productionRouteAttached",
    )

    @field_serializer(
        "runner_spawned",
        "live_tools_attached",
        "browser_session_started",
        "production_route_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class GeneralAutomationPresetProjection(BaseModel):
    model_config = _MODEL_CONFIG

    role_id: str = Field(alias="roleId")
    title: str
    tool_categories: tuple[str, ...] = Field(alias="toolCategories")
    allowed_permissions: tuple[PermissionName, ...] = Field(alias="allowedPermissions")
    enabled_browser_actions: tuple[BrowserAction, ...] = Field(alias="enabledBrowserActions")
    approval_required_actions: tuple[BrowserAction, ...] = Field(alias="approvalRequiredActions")
    approval_required_categories: tuple[str, ...] = Field(alias="approvalRequiredCategories")
    adk_agent_role: Mapping[str, object] = Field(alias="adkAgentRole")
    alias_ignored_reason_codes: tuple[str, ...] = Field(
        default=(),
        alias="aliasIgnoredReasonCodes",
    )
    recipe_owned: Literal[True] = Field(default=True, alias="recipeOwned")
    core_owned: Literal[False] = Field(default=False, alias="coreOwned")
    spawns_child_runners: Literal[False] = Field(default=False, alias="spawnsChildRunners")
    authority_flags: GeneralAutomationPresetAuthorityFlags = Field(
        default_factory=GeneralAutomationPresetAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("role_id")
    @classmethod
    def _validate_role_id(cls, value: str) -> str:
        if value not in GENERAL_AUTOMATION_PRESET_IDS:
            raise ValueError("unknown general automation preset role")
        return value

    def public_projection(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="json")


def compile_general_automation_presets() -> tuple[GeneralAutomationPresetProjection, ...]:
    return tuple(project_general_automation_preset(preset.role_id) for preset in general_automation_preset_catalog())


def project_general_automation_preset(
    role_id: str,
    *,
    alias_metadata: Mapping[str, object] | None = None,
) -> GeneralAutomationPresetProjection:
    preset = get_general_automation_preset(role_id)
    ignored = _alias_ignored_reason_codes(preset, alias_metadata or {})

    return GeneralAutomationPresetProjection(
        roleId=preset.role_id,
        title=preset.title,
        toolCategories=preset.tool_categories,
        allowedPermissions=preset.allowed_permissions,
        enabledBrowserActions=preset.enabled_browser_actions,
        approvalRequiredActions=preset.approval_required_actions,
        approvalRequiredCategories=preset.approval_required_categories,
        adkAgentRole=dict(preset.adk_agent_role_metadata),
        aliasIgnoredReasonCodes=ignored,
        recipeOwned=preset.recipe_owned,
        coreOwned=preset.core_owned,
        spawnsChildRunners=preset.spawns_child_runners,
    )


def _alias_ignored_reason_codes(
    preset: GeneralAutomationPreset,
    alias_metadata: Mapping[str, object],
) -> tuple[str, ...]:
    ignored: list[str] = []
    actions = _string_tuple(alias_metadata.get("enabledBrowserActions"))
    categories = _string_tuple(alias_metadata.get("toolCategories"))
    if preset.role_id != "automation.browser-act" and _BROWSER_ESCALATION_ACTIONS.intersection(actions):
        ignored.append("alias_browser_action_escalation_ignored")
    if preset.role_id != "automation.browser-act" and _BROWSER_ESCALATION_CATEGORIES.intersection(categories):
        ignored.append("alias_tool_category_escalation_ignored")
    return tuple(ignored)


def _string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, tuple | list):
        return tuple(str(item) for item in value)
    return ()


__all__ = [
    "GeneralAutomationPresetAuthorityFlags",
    "GeneralAutomationPresetProjection",
    "MUTATION_TOOL_CATEGORIES",
    "compile_general_automation_presets",
    "project_general_automation_preset",
]
