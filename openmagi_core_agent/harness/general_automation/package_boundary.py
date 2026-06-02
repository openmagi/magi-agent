from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from openmagi_core_agent.harness.general_automation.package_manifest import (
    AutomationPackageManifest,
)


PackageBoundaryStatus = Literal["accepted", "blocked"]

PROTECTED_TOOL_NAMES = frozenset(
    {
        "bash",
        "fileread",
        "filewrite",
        "fileedit",
        "grep",
        "glob",
        "webfetch",
        "websearch",
        "browser",
        "task",
        "spawnagent",
        "filedeliver",
        "filesend",
    }
)


class PackageBoundaryDecision(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    status: PackageBoundaryStatus
    package_ref: str = Field(alias="packageRef")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    executable_attachment_allowed: Literal[False] = Field(
        default=False,
        alias="executableAttachmentAllowed",
    )
    dependency_install_allowed: Literal[False] = Field(
        default=False,
        alias="dependencyInstallAllowed",
    )


class AutomationPackageBoundary:
    """Metadata-only boundary for first-party automation package inspection."""

    def inspect_manifest(self, manifest: AutomationPackageManifest) -> PackageBoundaryDecision:
        protected_collision = next(
            (
                tool.name
                for tool in manifest.tools
                if tool.name.casefold() in PROTECTED_TOOL_NAMES
            ),
            None,
        )
        if protected_collision is not None:
            return PackageBoundaryDecision(
                status="blocked",
                packageRef=manifest.package_ref,
                reasonCodes=("protected_tool_name_collision",),
            )

        if any(tool.execution_attachment == "requested" for tool in manifest.tools):
            return PackageBoundaryDecision(
                status="blocked",
                packageRef=manifest.package_ref,
                reasonCodes=("execution_attachment_disabled",),
            )

        return PackageBoundaryDecision(
            status="accepted",
            packageRef=manifest.package_ref,
            reasonCodes=(),
        )


__all__ = [
    "AutomationPackageBoundary",
    "PackageBoundaryDecision",
    "PROTECTED_TOOL_NAMES",
]
