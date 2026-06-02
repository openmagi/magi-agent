from __future__ import annotations

import subprocess
import sys


PRIVATE_KEY_BLOCK = "\n".join(
    (
        "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
        "SUPERSECRETKEYBODY",
        "-----END OPENSSH " + "PRIVATE KEY-----",
    )
)


class FakeMcpProvider:
    openmagi_local_fake_provider = True

    def __init__(self, *, auth_fail: bool = False) -> None:
        self.auth_fail = auth_fail
        self.list_calls = 0
        self.call_calls = 0

    def list_tools(self, server_ref: str) -> list[dict[str, object]]:
        self.list_calls += 1
        if self.auth_fail:
            from openmagi_core_agent.plugins.mcp_adapter import McpAuthError

            raise McpAuthError("Authorization: Bearer unsafe-token")
        return [
            {
                "name": "read_note",
                "description": "Read a selected note",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "noteName": {"type": "string"},
                        "apiToken": {"type": "string"},
                    },
                    "required": ["noteName", "apiToken"],
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "publish_change",
                "description": "Publish a change",
                "inputSchema": {"type": "object", "additionalProperties": False},
                "annotations": {"destructiveHint": True},
            },
        ]

    def call_tool(self, server_ref: str, tool_name: str, arguments: object) -> dict[str, object]:
        self.call_calls += 1
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "public response /private/var/folders/token "
                        f"/tmp/openmagi-workspace-abc/secret {PRIVATE_KEY_BLOCK}"
                    ),
                }
            ],
            "private": "Authorization: Bearer unsafe-token /Users/kevin/private",
        }


class UntrustedMcpProvider(FakeMcpProvider):
    openmagi_local_fake_provider = False


def _security_manifest(*, permissions: tuple[str, ...] = ("read", "write")) -> dict[str, object]:
    return {
        "serverRef": "mcp:notes",
        "trustLevel": "local_dev",
        "sandboxMode": "in_process_contract_only",
        "allowedPermissions": permissions,
        "supplyChainDigest": "sha256:" + "a" * 64,
    }


def test_mcp_adapter_default_off_does_not_call_provider() -> None:
    from openmagi_core_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    provider = FakeMcpProvider()
    decision = McpAdapter(McpAdapterConfig()).list_tools("mcp:notes", provider=provider)

    assert decision.status == "disabled"
    assert decision.reason_codes == ("mcp_adapter_disabled",)
    assert decision.manifests == ()
    assert provider.list_calls == 0
    assert set(decision.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_mcp_tools_list_converts_to_tool_manifests_with_permission_annotations() -> None:
    from openmagi_core_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    provider = FakeMcpProvider()
    decision = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).list_tools("mcp:notes", provider=provider, security_manifest=_security_manifest())

    assert decision.status == "ok"
    assert provider.list_calls == 1
    assert [manifest.name for manifest in decision.manifests] == [
        "mcp.notes.read_note",
        "mcp.notes.publish_change",
    ]
    read_manifest, write_manifest = decision.manifests
    assert read_manifest.permission == "read"
    assert read_manifest.side_effect_class == "none"
    assert read_manifest.parallel_safety == "readonly"
    assert read_manifest.should_defer is True
    assert read_manifest.adk_tool_type == "FunctionTool"
    assert read_manifest.input_schema["properties"] == {
        "noteName": {"type": "string"},
    }
    assert read_manifest.input_schema["required"] == ["noteName"]
    assert write_manifest.permission == "write"
    assert write_manifest.side_effect_class == "external"
    assert write_manifest.dangerous is True
    assert write_manifest.should_defer is True
    assert write_manifest.capability_tags == ("mcp", "mcp:notes", "destructive")


def test_mcp_tools_list_requires_security_manifest_before_provider_call() -> None:
    from openmagi_core_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    provider = FakeMcpProvider()
    decision = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).list_tools("mcp:notes", provider=provider)

    assert decision.status == "blocked"
    assert decision.reason_codes == ("mcp_security_manifest_required",)
    assert decision.manifests == ()
    assert provider.list_calls == 0


def test_mcp_tools_list_blocks_tools_outside_permission_manifest() -> None:
    from openmagi_core_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    provider = FakeMcpProvider()
    decision = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).list_tools(
        "mcp:notes",
        provider=provider,
        security_manifest=_security_manifest(permissions=("read",)),
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == ("mcp_tool_permission_not_allowed_by_manifest",)
    assert decision.manifests == ()


def test_mcp_tools_list_blocks_unavailable_sandbox_before_provider_call() -> None:
    from openmagi_core_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    provider = FakeMcpProvider()
    decision = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).list_tools(
        "mcp:notes",
        provider=provider,
        security_manifest={**_security_manifest(), "sandboxMode": "external_sandbox"},
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == ("mcp_sandbox_mode_not_available",)
    assert provider.list_calls == 0


def test_mcp_tools_list_requires_net_permission_for_open_world_tools() -> None:
    from openmagi_core_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    class OpenWorldProvider(FakeMcpProvider):
        def list_tools(self, server_ref: str) -> list[dict[str, object]]:
            self.list_calls += 1
            return [
                {
                    "name": "publish_change",
                    "description": "Publish a change",
                    "inputSchema": {"type": "object", "additionalProperties": False},
                    "annotations": {"destructiveHint": True, "openWorldHint": True},
                },
            ]

    decision = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).list_tools(
        "mcp:notes",
        provider=OpenWorldProvider(),
        security_manifest=_security_manifest(permissions=("read", "write")),
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == ("mcp_tool_permission_not_allowed_by_manifest",)


def test_mcp_tools_list_digests_private_tool_names_before_projection() -> None:
    from openmagi_core_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig
    from openmagi_core_agent.tools.tool_search import ToolSearchBoundary, ToolSearchConfig, ToolSearchRequest

    class PrivateNameProvider(FakeMcpProvider):
        def list_tools(self, server_ref: str) -> list[dict[str, object]]:
            self.list_calls += 1
            return [
                {
                    "name": "read_/Users/kevin/private_sk-test-unsafe0000",
                    "description": "Read a selected note",
                    "inputSchema": {"type": "object", "additionalProperties": False},
                    "annotations": {"readOnlyHint": True},
                },
            ]

    manifest = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).list_tools(
        "mcp:notes",
        provider=PrivateNameProvider(),
        security_manifest=_security_manifest(permissions=("read",)),
    ).manifests[0]
    projection = ToolSearchBoundary(ToolSearchConfig(enabled=True)).search(
        (manifest,),
        ToolSearchRequest(query="read", selectedToolNames=(manifest.name,)),
    ).public_projection()
    rendered = str(projection)

    assert manifest.name.startswith("mcp.notes.tool_")
    assert "/Users/kevin" not in rendered
    assert "sk-test-unsafe" not in rendered


def test_tool_search_public_projection_omits_raw_tool_names() -> None:
    from openmagi_core_agent.tools.manifest import Budget, ToolManifest, ToolSource
    from openmagi_core_agent.tools.tool_search import ToolSearchBoundary, ToolSearchConfig, ToolSearchRequest

    manifest = ToolManifest(
        name="internal_runtime_only_tool",
        description="Runtime helper internal_runtime_only_tool",
        kind="external",
        source=ToolSource(kind="runtime", package="test"),
        permission="read",
        input_schema={
            "type": "object",
            "properties": {
                "internal_runtime_only_tool": {
                    "type": "string",
                    "description": "internal_runtime_only_tool argument",
                },
            },
            "additionalProperties": False,
        },
        timeout_ms=30_000,
        budget=Budget(max_calls_per_turn=1, max_parallel=1),
        parallel_safety="readonly",
        is_concurrency_safe=True,
        tags=("internal_runtime_only_tool",),
    )

    projection = ToolSearchBoundary(ToolSearchConfig(enabled=True)).search(
        (manifest,),
        ToolSearchRequest(query="runtime", selectedToolNames=(manifest.name,)),
    ).public_projection()
    rendered = str(projection)

    assert "tool:" in rendered
    assert "internal_runtime_only_tool" not in rendered


def test_mcp_tools_list_drops_sensitive_schema_property_names() -> None:
    from openmagi_core_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    manifest = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).list_tools(
        "mcp:notes",
        provider=FakeMcpProvider(),
        security_manifest=_security_manifest(),
    ).manifests[0]

    assert manifest.input_schema["properties"] == {
        "noteName": {"type": "string"},
    }
    assert manifest.input_schema["required"] == ["noteName"]


def test_mcp_tools_without_explicit_readonly_annotation_are_conservative() -> None:
    from openmagi_core_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    class AmbiguousProvider(FakeMcpProvider):
        def list_tools(self, server_ref: str) -> list[dict[str, object]]:
            self.list_calls += 1
            return [
                {
                    "name": "ambiguous",
                    "description": "Ambiguous external tool",
                    "inputSchema": {"type": "object", "additionalProperties": False},
                    "annotations": {},
                }
            ]

    manifest = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).list_tools(
        "mcp:notes",
        provider=AmbiguousProvider(),
        security_manifest=_security_manifest(permissions=("net",)),
    ).manifests[0]

    assert manifest.permission == "net"
    assert manifest.side_effect_class == "external"
    assert manifest.parallel_safety == "unsafe"


def test_mcp_tools_list_blocks_untrusted_fake_provider() -> None:
    from openmagi_core_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    provider = UntrustedMcpProvider()
    decision = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).list_tools("mcp:notes", provider=provider, security_manifest=_security_manifest())

    assert decision.status == "blocked"
    assert decision.reason_codes == ("local_fake_mcp_provider_untrusted",)
    assert provider.list_calls == 0


def test_mcp_call_result_is_budgeted_and_auth_failure_is_non_crashing() -> None:
    from openmagi_core_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    adapter = McpAdapter(McpAdapterConfig(enabled=True, localFakeProviderEnabled=True))
    provider = FakeMcpProvider()
    manifest = adapter.list_tools(
        "mcp:notes",
        provider=provider,
        security_manifest=_security_manifest(),
    ).manifests[0]

    blocked = adapter.call_tool(
        manifest,
        {"noteName": "note.md"},
        provider=provider,
        server_ref="mcp:notes",
        security_manifest=_security_manifest(permissions=("read",)),
    )
    auth = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).list_tools(
        "mcp:notes",
        provider=FakeMcpProvider(auth_fail=True),
        security_manifest=_security_manifest(),
    )

    projection = blocked.public_projection()
    auth_projection = auth.public_projection()
    encoded = str({"blocked": projection, "auth": auth_projection})
    assert blocked.status == "ok"
    assert blocked.reason_codes == ()
    assert blocked.budgeted_result is not None
    assert blocked.budgeted_result.status == "ok"
    assert blocked.receipt_ref is not None
    assert projection["result"]["status"] == "ok"
    assert "Authorization" not in encoded
    assert "unsafe-token" not in encoded
    assert "/Users/kevin" not in encoded
    assert "/private/var" not in encoded
    assert "/tmp/openmagi-workspace" not in encoded
    assert "PRIVATE KEY" not in encoded
    assert "SUPERSECRETKEYBODY" not in encoded
    assert "public response" in encoded
    assert provider.call_calls == 1
    assert auth.status == "auth_required"
    assert auth.reason_codes == ("mcp_auth_required",)


def test_mcp_call_requires_manifest_and_blocks_permission_mismatch_before_provider() -> None:
    from openmagi_core_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    adapter = McpAdapter(McpAdapterConfig(enabled=True, localFakeProviderEnabled=True))
    provider = FakeMcpProvider()
    manifest = adapter.list_tools(
        "mcp:notes",
        provider=provider,
        security_manifest=_security_manifest(),
    ).manifests[0]

    missing = adapter.call_tool(manifest, {"noteName": "note.md"}, provider=provider, server_ref="mcp:notes")
    mismatch = adapter.call_tool(
        manifest,
        {"noteName": "note.md"},
        provider=provider,
        server_ref="mcp:notes",
        security_manifest=_security_manifest(permissions=("write",)),
    )

    assert missing.status == "blocked"
    assert missing.reason_codes == ("mcp_security_manifest_required",)
    assert mismatch.status == "blocked"
    assert mismatch.reason_codes == ("mcp_tool_permission_not_allowed_by_manifest",)
    assert provider.call_calls == 0


def test_mcp_call_blocks_manifest_server_ref_mismatch_before_provider() -> None:
    from openmagi_core_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    adapter = McpAdapter(McpAdapterConfig(enabled=True, localFakeProviderEnabled=True))
    provider = FakeMcpProvider()
    manifest = adapter.list_tools(
        "mcp:notes",
        provider=provider,
        security_manifest=_security_manifest(),
    ).manifests[0]

    decision = adapter.call_tool(
        manifest,
        {"noteName": "note.md"},
        provider=provider,
        server_ref="mcp:other",
        security_manifest={**_security_manifest(permissions=("read",)), "serverRef": "mcp:other"},
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == ("mcp_tool_server_ref_mismatch",)
    assert provider.call_calls == 0


def test_mcp_call_blocks_unavailable_sandbox_before_provider_call() -> None:
    from openmagi_core_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    adapter = McpAdapter(McpAdapterConfig(enabled=True, localFakeProviderEnabled=True))
    provider = FakeMcpProvider()
    manifest = adapter.list_tools(
        "mcp:notes",
        provider=provider,
        security_manifest=_security_manifest(),
    ).manifests[0]

    decision = adapter.call_tool(
        manifest,
        {"noteName": "note.md"},
        provider=provider,
        server_ref="mcp:notes",
        security_manifest={**_security_manifest(permissions=("read",)), "sandboxMode": "external_sandbox"},
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == ("mcp_sandbox_mode_not_available",)
    assert provider.call_calls == 0


def test_mcp_call_default_off_does_not_execute_provider() -> None:
    from openmagi_core_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    adapter = McpAdapter(McpAdapterConfig())
    provider = FakeMcpProvider()
    manifest = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).list_tools("mcp:notes", provider=provider, security_manifest=_security_manifest()).manifests[0]
    decision = adapter.call_tool(
        manifest,
        {},
        provider=provider,
        server_ref="mcp:notes",
        security_manifest=_security_manifest(permissions=("read",)),
    )

    assert decision.status == "disabled"
    assert decision.reason_codes == ("mcp_adapter_disabled",)
    assert provider.call_calls == 0


def test_mcp_decision_projection_ignores_forged_authority_flags() -> None:
    from openmagi_core_agent.plugins.mcp_adapter import McpListDecision

    forged = McpListDecision.model_construct(
        status="ok",
        manifests=(),
        reasonCodes=(),
        authorityFlags={"mcpServerAttached": True, "liveToolExecutionEnabled": True},
    )

    assert set(forged.public_projection()["authorityFlags"].values()) == {False}


def test_mcp_adapter_imports_no_live_mcp_or_network_clients() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("openmagi_core_agent.plugins.mcp_adapter")
forbidden = (
    "mcp",
    "subprocess",
    "requests",
    "httpx",
    "aiohttp",
    "socket",
    "openai",
    "anthropic",
    "google.adk.runners",
    "openmagi_core_agent.runtime.route_activation",
    "openmagi_core_agent.runtime.adk_turn_runner",
    "openmagi_core_agent.runtime.model_routing",
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.transport.chat_route",
    "openmagi_core_agent.tools.kernel",
    "openmagi_core_agent.tools.dispatcher",
    "openmagi_core_agent.deploy",
    "openmagi_core_agent.provisioning",
)
forbidden_prefixes = (
    "openmagi_core_agent.adk_bridge.",
    "openmagi_core_agent.deploy.",
    "openmagi_core_agent.provisioning.",
)
loaded = [
    name
    for name in sys.modules
    if name in forbidden or any(name.startswith(prefix) for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"forbidden modules loaded: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
