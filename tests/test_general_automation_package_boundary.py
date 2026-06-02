from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.harness.general_automation.package_boundary import (
    AutomationPackageBoundary,
    PackageBoundaryDecision,
)
from magi_agent.harness.general_automation.package_manifest import (
    AutomationPackageManifest,
)
from magi_agent.harness.general_automation.package_tool_projection import (
    project_automation_package_tools,
)


PYTHON_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = PYTHON_ROOT / "magi_agent" / "harness" / "general_automation"


def _base_manifest() -> dict[str, object]:
    return {
        "packageId": "acme.reports",
        "version": "1.2.3",
        "publisher": "acme",
        "signed": True,
        "signatureDigest": "sha256:" + "a" * 64,
        "dependencies": [
            {
                "name": "acme-report-schema",
                "version": "2.0.0",
                "implicitInstall": False,
            }
        ],
        "tools": [
            {
                "name": "AcmeReportSummarize",
                "description": "Summarize an approved report export.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"artifactRef": {"type": "string"}},
                    "required": ["artifactRef"],
                    "additionalProperties": False,
                },
                "permission": "read",
                "sideEffectClass": "none",
                "parallelSafety": "readonly",
                "outputBudget": {"outputChars": 2000, "transcriptChars": 500},
                "credentialHandles": [
                    {
                        "handle": "credential:acme-reports-read",
                        "purpose": "read approved report exports",
                    }
                ],
                "executionAttachment": "none",
                "adkToolType": "FunctionTool",
            }
        ],
    }


def test_package_boundary_accepts_metadata_only_manifest_without_execution() -> None:
    manifest = AutomationPackageManifest.model_validate(_base_manifest())
    decision = AutomationPackageBoundary().inspect_manifest(manifest)

    assert decision == PackageBoundaryDecision(
        status="accepted",
        package_ref="automation-package:acme.reports@1.2.3",
        reason_codes=(),
        executable_attachment_allowed=False,
        dependency_install_allowed=False,
    )


def test_unsigned_package_cannot_request_execution_attachment() -> None:
    data = _base_manifest()
    data["signed"] = False
    data["signatureDigest"] = None
    data["tools"][0]["executionAttachment"] = "requested"  # type: ignore[index]

    with pytest.raises(ValidationError, match="unsigned packages cannot request execution attachment"):
        AutomationPackageManifest.model_validate(data)


def test_dependencies_are_declared_but_never_implicitly_installed() -> None:
    data = _base_manifest()
    data["dependencies"][0]["implicitInstall"] = True  # type: ignore[index]

    with pytest.raises(ValidationError, match="dependencies must not install implicitly"):
        AutomationPackageManifest.model_validate(data)


def test_raw_credential_material_is_rejected_in_tool_declarations() -> None:
    data = _base_manifest()
    data["tools"][0]["credentialHandles"][0]["token"] = "secret-value"  # type: ignore[index]

    with pytest.raises(ValidationError):
        AutomationPackageManifest.model_validate(data)


def test_protected_builtin_tool_names_cannot_be_projected() -> None:
    data = _base_manifest()
    data["tools"][0]["name"] = "FileRead"  # type: ignore[index]
    manifest = AutomationPackageManifest.model_validate(data)

    decision = AutomationPackageBoundary().inspect_manifest(manifest)

    assert decision.status == "blocked"
    assert decision.reason_codes == ("protected_tool_name_collision",)
    with pytest.raises(ValueError, match="protected_tool_name_collision"):
        project_automation_package_tools(manifest)


def test_package_tools_project_to_disabled_function_tool_metadata() -> None:
    manifest = AutomationPackageManifest.model_validate(_base_manifest())

    projected = project_automation_package_tools(manifest)

    assert len(projected) == 1
    tool = projected[0]
    assert tool.name == "AcmeReportSummarize"
    assert tool.kind == "custom"
    assert tool.source.kind == "custom-plugin"
    assert tool.source.package == "acme.reports"
    assert tool.permission == "read"
    assert tool.input_schema["required"] == ["artifactRef"]
    assert tool.enabled_by_default is False
    assert tool.timeout_ms == 0
    assert tool.adk_tool_type == "FunctionTool"
    assert tool.side_effect_class == "none"
    assert tool.parallel_safety == "readonly"
    assert tool.budget.output_chars == 2000
    assert "metadata-only" in tool.tags
    assert "execution-disabled" in tool.preconditions


def test_package_boundary_modules_do_not_import_or_execute_package_code() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE_DIR.glob("package_*.py"))

    forbidden_fragments = (
        "importlib",
        "subprocess",
        "runpy",
        "exec(",
        "eval(",
        "__import__",
        "builtins.compile",
    )
    for fragment in forbidden_fragments:
        assert fragment not in source
