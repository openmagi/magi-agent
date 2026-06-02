from __future__ import annotations

import subprocess
import sys

from magi_agent.plugins.extension_boundary import (
    ExtensionBoundary,
    ExtensionBoundaryConfig,
    ExtensionBoundaryRequest,
)


class FakeExtensionProvider:
    openmagi_local_fake_provider = True

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def preview_extension(self, request: ExtensionBoundaryRequest) -> dict[str, object]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("extension failed /Users/kevin/private ghp_extensionSecret")
        return {
            "extensionId": request.extension_id,
            "kind": request.kind,
            "manifestRef": "manifest:skill-1",
            "capabilities": ("tool:read", "hook:review"),
            "evidenceRef": "evidence:extension-1",
            "rawManifest": "/workspace/private",
        }


def test_extension_boundary_is_disabled_by_default() -> None:
    provider = FakeExtensionProvider()
    decision = ExtensionBoundary(ExtensionBoundaryConfig()).execute(
        ExtensionBoundaryRequest(operation="skill.load", extensionId="skill:superpowers"),
        provider=provider,
    )

    assert decision.status == "disabled"
    assert decision.reason_codes == ("extension_boundary_disabled",)
    assert provider.calls == 0
    assert set(decision.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_extension_boundary_projects_skill_tool_hook_and_mcp_previews_with_fake_provider() -> None:
    provider = FakeExtensionProvider()
    boundary = ExtensionBoundary(
        ExtensionBoundaryConfig(enabled=True, localFakeExtensionProviderEnabled=True),
    )

    operations = (
        ("skill.load", "skill:superpowers", "skill"),
        ("external_tool.load", "tool:custom", "external_tool"),
        ("runtime_hook.load", "hook:review", "runtime_hook"),
        ("mcp_server.load", "mcp:filesystem", "mcp_server"),
        ("mcp_tool.project", "mcp-tool:list", "mcp_tool"),
    )
    projections = [
        boundary.execute(
            ExtensionBoundaryRequest(operation=operation, extensionId=extension_id, kind=kind),
            provider=provider,
        ).public_projection()
        for operation, extension_id, kind in operations
    ]

    assert provider.calls == len(operations)
    assert [projection["status"] for projection in projections] == ["projected_local_fake"] * len(operations)
    assert [projection["preview"]["kind"] for projection in projections] == [item[2] for item in operations]
    assert all(projection["authorityFlags"]["externalCodeExecuted"] is False for projection in projections)
    assert all(projection["authorityFlags"]["mcpServerAttached"] is False for projection in projections)


def test_extension_boundary_blocks_protected_runtime_hooks_and_untrusted_provider() -> None:
    class UnmarkedProvider(FakeExtensionProvider):
        openmagi_local_fake_provider = False

    boundary = ExtensionBoundary(
        ExtensionBoundaryConfig(enabled=True, localFakeExtensionProviderEnabled=True),
    )

    protected = boundary.execute(
        ExtensionBoundaryRequest(
            operation="runtime_hook.load",
            extensionId="hook:protected",
            protectedRuntimeHook=True,
        ),
        provider=FakeExtensionProvider(),
    )
    untrusted = boundary.execute(
        ExtensionBoundaryRequest(operation="skill.load", extensionId="skill:unsafe"),
        provider=UnmarkedProvider(),
    )

    assert protected.status == "blocked"
    assert protected.reason_codes == ("protected_runtime_hook_blocked",)
    assert untrusted.status == "blocked"
    assert untrusted.reason_codes == ("local_fake_extension_provider_untrusted",)


def test_extension_boundary_sanitizes_provider_errors_and_metadata() -> None:
    decision = ExtensionBoundary(
        ExtensionBoundaryConfig(enabled=True, localFakeExtensionProviderEnabled=True),
    ).execute(
        ExtensionBoundaryRequest(
            operation="external_tool.load",
            extensionId="tool:safe",
            metadata={
                "note": "safe",
                "rawManifest": "/workspace/private",
                "apiToken": "123456:ABC-secret-token",
            },
        ),
        provider=FakeExtensionProvider(fail=True),
    )

    projection = decision.public_projection()
    encoded = str(projection)
    assert decision.status == "blocked"
    assert decision.reason_codes == ("local_fake_extension_provider_error",)
    assert "/Users/kevin" not in encoded
    assert "/workspace/private" not in encoded
    assert "ghp_extensionSecret" not in encoded
    assert "123456:ABC-secret-token" not in encoded
    assert projection["diagnosticMetadata"]["note"] == "safe"


def test_extension_boundary_has_no_live_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.plugins.extension_boundary")
forbidden = (
    "google.adk.runners",
    "mcp",
    "subprocess",
    "requests",
    "httpx",
)
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise AssertionError(f"forbidden modules loaded: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
