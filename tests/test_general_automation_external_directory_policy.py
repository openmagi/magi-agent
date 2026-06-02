from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.harness.general_automation.external_directory_receipts import (
    build_external_directory_approval_receipt,
    project_external_directory_denial,
)
from magi_agent.harness.general_automation.path_policy import (
    PathAccessRequest,
    classify_path_access,
)


PYTHON_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = PYTHON_ROOT / "magi_agent" / "harness" / "general_automation"


def _contains_fragment(value: object, fragment: str) -> bool:
    if isinstance(value, str):
        return fragment in value
    if isinstance(value, dict):
        return any(
            _contains_fragment(key, fragment) or _contains_fragment(item, fragment)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_fragment(item, fragment) for item in value)
    return False


def _request(path: str, operation: str = "read") -> PathAccessRequest:
    return PathAccessRequest(
        workspaceRoot="/Users/acme/workspace",
        homeDir="/Users/acme",
        path=path,
        operationClass=operation,
    )


def test_workspace_local_paths_classify_without_external_approval() -> None:
    decision = classify_path_access(_request("reports/q1.csv"))

    assert decision.status == "workspace_local"
    assert decision.approval_required is False
    assert decision.canonical_path_prefix == "/Users/acme/workspace"
    assert decision.operation_class == "read"

    public = decision.public_projection()
    assert public["status"] == "workspace_local"
    assert public["approvalRequired"] is False
    assert public["pathDigest"].startswith("sha256:")
    assert not _contains_fragment(public, "/Users/acme")


@pytest.mark.parametrize(
    ("raw_path", "expected_prefix"),
    (
        ("~/Downloads/import.csv", "/Users/acme/Downloads"),
        ("/tmp/browser-download.csv", "/tmp"),
        ("/Volumes/Shared/report.xlsx", "/Volumes/Shared"),
        ("/Users/acme/Desktop/export.pdf", "/Users/acme/Desktop"),
        ("/mnt/project-share/import.csv", "/mnt/project-share"),
    ),
)
def test_workspace_adjacent_paths_classify_as_external_directory_access(
    raw_path: str,
    expected_prefix: str,
) -> None:
    decision = classify_path_access(_request(raw_path, operation="write"))

    assert decision.status == "external_directory"
    assert decision.approval_required is True
    assert decision.canonical_path_prefix == expected_prefix
    assert decision.operation_class == "write"
    assert decision.reason_codes == ("external_directory_approval_required",)


def test_external_directory_approval_receipt_has_digest_safe_public_projection() -> None:
    decision = classify_path_access(_request("~/Downloads/import.csv", operation="read"))

    receipt = build_external_directory_approval_receipt(
        decision,
        approvalRef="approval:external-directory:sha256:" + "a" * 64,
    )

    assert receipt.status == "approval_required"
    assert receipt.canonical_path_prefix == "/Users/acme/Downloads"
    assert receipt.operation_class == "read"
    assert receipt.path_digest == decision.path_digest
    assert receipt.adk_control_kind == "tool_callback_control_request"

    public = receipt.public_projection()
    assert public["status"] == "approval_required"
    assert public["operationClass"] == "read"
    assert public["pathDigest"] == decision.path_digest
    assert public["canonicalPathPrefixDigest"].startswith("sha256:")
    assert not _contains_fragment(public, "/Users/acme")
    assert not _contains_fragment(public, "Downloads")


def test_external_directory_denial_projects_model_visible_blocked_result() -> None:
    decision = classify_path_access(_request("/Volumes/Shared/report.xlsx", operation="read"))
    receipt = build_external_directory_approval_receipt(
        decision,
        approvalRef="approval:external-directory:sha256:" + "b" * 64,
    )

    blocked = project_external_directory_denial(
        receipt,
        denialReason="user_denied_external_directory_access",
    )

    public = blocked.model_dump(by_alias=True, mode="json")
    assert public["status"] == "blocked"
    assert public["errorCode"] == "external_directory_access_denied"
    assert public["modelVisible"] is True
    assert public["approvalRequired"] is False
    assert public["operationClass"] == "read"
    assert public["pathDigest"] == decision.path_digest
    assert "user_denied_external_directory_access" in public["reasonCodes"]
    assert not _contains_fragment(public, "/Volumes")
    assert not _contains_fragment(public, "Shared")


def test_external_directory_policy_modules_do_not_import_core_permission_or_touch_files() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            PACKAGE_DIR / "path_policy.py",
            PACKAGE_DIR / "external_directory_receipts.py",
        )
    )

    forbidden_fragments: tuple[str, ...] = (
        "tools.permission",
        "Path(",
        ".read_text(",
        ".write_text(",
        ".exists(",
        ".is_file(",
        ".mkdir(",
        "open(",
    )
    for fragment in forbidden_fragments:
        assert fragment not in source
