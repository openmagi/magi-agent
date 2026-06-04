from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PermissionName = Literal["read", "write", "execute", "net", "meta"]
BrowserAction = Literal["click", "fill", "download", "submit"]

GENERAL_AUTOMATION_PRESET_IDS: tuple[str, ...] = (
    "automation.plan",
    "automation.research",
    "automation.files",
    "automation.office",
    "automation.browser-inspect",
    "automation.browser-act",
    "automation.scout",
)

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_SAFE_TOOL_CATEGORY_RE = r"^[a-z][a-z0-9_-]{0,80}$"


class GeneralAutomationPreset(BaseModel):
    model_config = _MODEL_CONFIG

    role_id: str = Field(alias="roleId")
    title: str
    tool_categories: tuple[str, ...] = Field(alias="toolCategories")
    allowed_permissions: tuple[PermissionName, ...] = Field(alias="allowedPermissions")
    enabled_browser_actions: tuple[BrowserAction, ...] = Field(
        default=(),
        alias="enabledBrowserActions",
    )
    approval_required_actions: tuple[BrowserAction, ...] = Field(
        default=(),
        alias="approvalRequiredActions",
    )
    approval_required_categories: tuple[str, ...] = Field(
        default=(),
        alias="approvalRequiredCategories",
    )
    adk_agent_role_metadata: Mapping[str, object] = Field(alias="adkAgentRoleMetadata")
    recipe_owned: Literal[True] = Field(default=True, alias="recipeOwned")
    core_owned: Literal[False] = Field(default=False, alias="coreOwned")
    spawns_child_runners: Literal[False] = Field(default=False, alias="spawnsChildRunners")

    @field_validator("role_id")
    @classmethod
    def _validate_role_id(cls, value: str) -> str:
        if value not in GENERAL_AUTOMATION_PRESET_IDS:
            raise ValueError("unknown general automation preset role")
        return value

    @field_validator("tool_categories", "approval_required_categories")
    @classmethod
    def _validate_categories(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        import re

        for item in value:
            if not re.fullmatch(_SAFE_TOOL_CATEGORY_RE, item):
                raise ValueError("tool categories must be safe public identifiers")
        return value

    @model_validator(mode="after")
    def _validate_role_metadata(self) -> Self:
        if self.adk_agent_role_metadata.get("roleId") != self.role_id:
            raise ValueError("ADK role metadata must reference the preset role")
        if self.enabled_browser_actions and self.role_id != "automation.browser-inspect":
            raise ValueError("only inspect presets may expose pre-approved browser actions")
        if self.role_id == "automation.browser-inspect" and any(
            action in self.enabled_browser_actions for action in ("download", "submit")
        ):
            raise ValueError("browser inspect cannot enable download or submit")
        return self


def general_automation_preset_catalog() -> tuple[GeneralAutomationPreset, ...]:
    return _PRESETS


def get_general_automation_preset(role_id: str) -> GeneralAutomationPreset:
    for preset in _PRESETS:
        if preset.role_id == role_id:
            return preset
    raise KeyError(role_id)


def _preset(
    role_id: str,
    title: str,
    *,
    tool_categories: tuple[str, ...],
    allowed_permissions: tuple[PermissionName, ...],
    enabled_browser_actions: tuple[BrowserAction, ...] = (),
    approval_required_actions: tuple[BrowserAction, ...] = (),
    approval_required_categories: tuple[str, ...] = (),
) -> GeneralAutomationPreset:
    return GeneralAutomationPreset(
        roleId=role_id,
        title=title,
        toolCategories=tool_categories,
        allowedPermissions=allowed_permissions,
        enabledBrowserActions=enabled_browser_actions,
        approvalRequiredActions=approval_required_actions,
        approvalRequiredCategories=approval_required_categories,
        adkAgentRoleMetadata={
            "roleId": role_id,
            "adkPrimitive": "Agent role metadata",
            "runnerAttached": False,
            "childRunnerStarted": False,
        },
    )


_PRESETS: tuple[GeneralAutomationPreset, ...] = (
    _preset(
        "automation.plan",
        "Plan",
        tool_categories=("reasoning", "workspace_read", "metadata", "user_question"),
        allowed_permissions=("read", "meta"),
    ),
    _preset(
        "automation.research",
        "Research",
        tool_categories=("web_search", "web_fetch_metadata", "source_ledger", "user_question"),
        allowed_permissions=("read", "net", "meta"),
    ),
    _preset(
        "automation.files",
        "Files",
        tool_categories=(
            "workspace_read",
            "workspace_write_policy",
            "snapshot",
            "external_directory_policy",
        ),
        allowed_permissions=("read", "write", "meta"),
        approval_required_categories=("workspace_write_policy", "external_directory_policy"),
    ),
    _preset(
        "automation.office",
        "Office",
        tool_categories=(
            "spreadsheet",
            "document",
            "artifact_evidence",
            "delivery_receipt_required",
        ),
        allowed_permissions=("read", "write", "meta"),
        approval_required_categories=("artifact_evidence", "delivery_receipt_required"),
    ),
    _preset(
        "automation.browser-inspect",
        "Browser Inspect",
        tool_categories=("browser_open", "browser_snapshot", "browser_scrape"),
        allowed_permissions=("read", "net", "meta"),
    ),
    _preset(
        "automation.browser-act",
        "Browser Act",
        tool_categories=(
            "browser_click",
            "browser_fill",
            "browser_download",
            "browser_submit",
        ),
        allowed_permissions=("read", "write", "net", "meta"),
        approval_required_actions=("click", "fill", "download", "submit"),
        approval_required_categories=("browser_click", "browser_fill", "browser_download", "browser_submit"),
    ),
    _preset(
        "automation.scout",
        "Scout",
        tool_categories=("external_docs", "repo_read", "web_read", "user_question"),
        allowed_permissions=("read", "net", "meta"),
    ),
)


__all__ = [
    "BrowserAction",
    "GENERAL_AUTOMATION_PRESET_IDS",
    "GeneralAutomationPreset",
    "PermissionName",
    "general_automation_preset_catalog",
    "get_general_automation_preset",
]
