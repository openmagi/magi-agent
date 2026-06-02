from __future__ import annotations

import copy

from magi_agent.harness.general_automation.package_boundary import (
    AutomationPackageBoundary,
)
from magi_agent.harness.general_automation.package_manifest import (
    AutomationPackageManifest,
    AutomationToolDeclaration,
)
from magi_agent.tools.manifest import Budget, ToolManifest, ToolSource


def project_automation_package_tools(
    manifest: AutomationPackageManifest,
) -> tuple[ToolManifest, ...]:
    """Project automation package tools as disabled ToolManifest metadata."""
    decision = AutomationPackageBoundary().inspect_manifest(manifest)
    if decision.status != "accepted":
        raise ValueError(",".join(decision.reason_codes))
    return tuple(_project_tool(manifest, tool) for tool in manifest.tools)


def _project_tool(
    manifest: AutomationPackageManifest,
    tool: AutomationToolDeclaration,
) -> ToolManifest:
    is_long_running = tool.adk_tool_type == "LongRunningFunctionTool"
    mutates_workspace = tool.side_effect_class in {"local_workspace", "local_and_external"}
    return ToolManifest(
        name=tool.name,
        description=tool.description,
        kind="custom",
        source=ToolSource(kind="custom-plugin", package=manifest.package_id),
        permission=tool.permission,
        inputSchema=copy.deepcopy(tool.input_schema),
        outputSchema=copy.deepcopy(tool.output_schema),
        dangerous=tool.permission in {"execute", "net"},
        mutatesWorkspace=mutates_workspace,
        tags=(
            "general-automation",
            "automation-package",
            manifest.package_id,
            "metadata-only",
        ),
        shouldDefer=is_long_running,
        capabilityTags=("automation-package",),
        sideEffectClass=tool.side_effect_class,
        parallelSafety=tool.parallel_safety,
        preconditions=(
            "execution-disabled",
            "approval-required-before-attachment",
            "dependency-install-disabled",
        ),
        latencyClass="background" if is_long_running else "inline",
        adkToolType=tool.adk_tool_type,
        timeoutMs=0,
        budget=Budget(
            outputChars=tool.output_budget.output_chars,
            transcriptChars=tool.output_budget.transcript_chars,
        ),
        plugin_id=manifest.package_id,
        enabled_by_default=False,
        opt_out=True,
    )


__all__ = ["project_automation_package_tools"]
