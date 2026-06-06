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
        self.list_prompt_calls = 0
        self.get_prompt_calls = 0

    def list_tools(self, server_ref: str) -> list[dict[str, object]]:
        self.list_calls += 1
        if self.auth_fail:
            from magi_agent.plugins.mcp_adapter import McpAuthError

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

    def list_prompts(self, server_ref: str) -> list[dict[str, object]]:
        self.list_prompt_calls += 1
        if self.auth_fail:
            from magi_agent.plugins.mcp_adapter import McpAuthError

            raise McpAuthError("Authorization: Bearer unsafe-token")
        return [
            {
                "name": "summarize_note",
                "description": "Summarize a selected note",
                "arguments": [
                    {"name": "noteName", "description": "the note to summarize", "required": True},
                    {"name": "tone", "description": "tone of voice", "required": False},
                ],
            },
            {
                "name": "draft_reply",
                "description": "Draft a reply",
                "arguments": [{"name": "topic"}],
            },
        ]

    def get_prompt(self, server_ref: str, prompt_name: str, arguments: object) -> dict[str, object]:
        self.get_prompt_calls += 1
        return {
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            "public prompt body /private/var/folders/token "
                            f"/Users/kevin/private {PRIVATE_KEY_BLOCK}"
                        ),
                    },
                }
            ],
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
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    provider = FakeMcpProvider()
    decision = McpAdapter(McpAdapterConfig()).list_tools("mcp:notes", provider=provider)

    assert decision.status == "disabled"
    assert decision.reason_codes == ("mcp_adapter_disabled",)
    assert decision.manifests == ()
    assert provider.list_calls == 0
    assert set(decision.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_mcp_tools_list_converts_to_tool_manifests_with_permission_annotations() -> None:
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

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
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    provider = FakeMcpProvider()
    decision = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).list_tools("mcp:notes", provider=provider)

    assert decision.status == "blocked"
    assert decision.reason_codes == ("mcp_security_manifest_required",)
    assert decision.manifests == ()
    assert provider.list_calls == 0


def test_mcp_tools_list_blocks_tools_outside_permission_manifest() -> None:
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

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
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

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
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

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
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig
    from magi_agent.tools.tool_search import ToolSearchBoundary, ToolSearchConfig, ToolSearchRequest

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
    from magi_agent.tools.manifest import Budget, ToolManifest, ToolSource
    from magi_agent.tools.tool_search import ToolSearchBoundary, ToolSearchConfig, ToolSearchRequest

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
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

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
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

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
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    provider = UntrustedMcpProvider()
    decision = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).list_tools("mcp:notes", provider=provider, security_manifest=_security_manifest())

    assert decision.status == "blocked"
    assert decision.reason_codes == ("local_fake_mcp_provider_untrusted",)
    assert provider.list_calls == 0


def test_mcp_call_result_is_budgeted_and_auth_failure_is_non_crashing() -> None:
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

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
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

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
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

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
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

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
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

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
    from magi_agent.plugins.mcp_adapter import McpListDecision

    forged = McpListDecision.model_construct(
        status="ok",
        manifests=(),
        reasonCodes=(),
        authorityFlags={"mcpServerAttached": True, "liveToolExecutionEnabled": True},
    )

    assert set(forged.public_projection()["authorityFlags"].values()) == {False}


# ---------------------------------------------------------------------------
# Prompt projection (P2) — mirrors the tools-path gating + redaction
# ---------------------------------------------------------------------------


def test_mcp_list_prompts_default_off_does_not_call_provider() -> None:
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    provider = FakeMcpProvider()
    decision = McpAdapter(McpAdapterConfig()).list_prompts("mcp:notes", provider=provider)

    assert decision.status == "disabled"
    assert decision.reason_codes == ("mcp_adapter_disabled",)
    assert decision.descriptors == ()
    assert provider.list_prompt_calls == 0
    assert set(decision.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_mcp_list_prompts_requires_security_manifest_before_provider_call() -> None:
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    provider = FakeMcpProvider()
    decision = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).list_prompts("mcp:notes", provider=provider)

    assert decision.status == "blocked"
    assert decision.reason_codes == ("mcp_security_manifest_required",)
    assert decision.descriptors == ()
    assert provider.list_prompt_calls == 0


def test_mcp_list_prompts_blocks_when_no_provider() -> None:
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    decision = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).list_prompts("mcp:notes", provider=None, security_manifest=_security_manifest())

    assert decision.status == "blocked"
    assert decision.reason_codes == ("local_fake_mcp_provider_required",)
    assert decision.descriptors == ()


def test_mcp_list_prompts_blocks_untrusted_fake_provider() -> None:
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    provider = UntrustedMcpProvider()
    decision = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).list_prompts("mcp:notes", provider=provider, security_manifest=_security_manifest())

    assert decision.status == "blocked"
    assert decision.reason_codes == ("local_fake_mcp_provider_untrusted",)
    assert provider.list_prompt_calls == 0


def test_mcp_list_prompts_happy_path_returns_redacted_descriptors() -> None:
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    provider = FakeMcpProvider()
    decision = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).list_prompts("mcp:notes", provider=provider, security_manifest=_security_manifest())

    assert decision.status == "ok"
    assert provider.list_prompt_calls == 1
    assert [d.name for d in decision.descriptors] == [
        "mcp.notes.summarize_note",
        "mcp.notes.draft_reply",
    ]
    summarize, draft = decision.descriptors
    assert summarize.description == "Summarize a selected note"
    assert summarize.arguments == ("notename", "tone")
    assert draft.arguments == ("topic",)


def test_mcp_list_prompts_public_projection_leaks_nothing_raw() -> None:
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    class PrivatePromptProvider(FakeMcpProvider):
        def list_prompts(self, server_ref: str) -> list[dict[str, object]]:
            self.list_prompt_calls += 1
            return [
                {
                    "name": "leak_/Users/kevin/private_sk-test-unsafe0000",
                    "description": "Reads /private/var/folders/token " + PRIVATE_KEY_BLOCK,
                    "arguments": [{"name": "path_/Users/kevin/secret"}],
                }
            ]

    decision = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).list_prompts(
        "mcp:notes",
        provider=PrivatePromptProvider(),
        security_manifest=_security_manifest(),
    )

    descriptor = decision.descriptors[0]
    rendered = str({"descriptor": descriptor.model_dump(), "projection": decision.public_projection()})

    # Private leaf name is digested by the shared ``_safe_tool_segment`` helper
    # (``tool_<digest>``) — what matters is no raw private text survives.
    assert descriptor.name.startswith("mcp.notes.tool_")
    assert "/Users/kevin" not in rendered
    assert "sk-test-unsafe" not in rendered
    assert "/private/var" not in rendered
    assert "PRIVATE KEY" not in rendered
    assert "SUPERSECRETKEYBODY" not in rendered
    # public_projection emits only digest refs, no raw names.
    projection = decision.public_projection()
    assert all(ref.startswith("prompt:") for ref in projection["promptRefs"])
    assert set(projection["authorityFlags"].values()) == {False}


def test_mcp_list_prompts_caps_at_max_prompts() -> None:
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    class ManyPromptsProvider(FakeMcpProvider):
        def list_prompts(self, server_ref: str) -> list[dict[str, object]]:
            self.list_prompt_calls += 1
            return [{"name": f"p{n}", "arguments": []} for n in range(10)]

    decision = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True, maxPrompts=3),
    ).list_prompts(
        "mcp:notes",
        provider=ManyPromptsProvider(),
        security_manifest=_security_manifest(),
    )

    assert decision.status == "ok"
    assert len(decision.descriptors) == 3


def test_mcp_list_prompts_skips_non_mapping_entries() -> None:
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    class MessyProvider(FakeMcpProvider):
        def list_prompts(self, server_ref: str) -> list[object]:
            self.list_prompt_calls += 1
            return [
                {"name": "good", "arguments": []},
                "not-a-mapping",
                123,
                {"name": "also_good", "arguments": [{"name": "x"}]},
            ]

    decision = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).list_prompts(
        "mcp:notes",
        provider=MessyProvider(),
        security_manifest=_security_manifest(),
    )

    assert decision.status == "ok"
    assert [d.name for d in decision.descriptors] == ["mcp.notes.good", "mcp.notes.also_good"]


def test_mcp_list_prompts_provider_error_returns_safe_digest() -> None:
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    class BoomProvider(FakeMcpProvider):
        def list_prompts(self, server_ref: str) -> list[dict[str, object]]:
            self.list_prompt_calls += 1
            raise RuntimeError("boom /Users/kevin/private Authorization: Bearer unsafe-token")

    decision = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).list_prompts(
        "mcp:notes",
        provider=BoomProvider(),
        security_manifest=_security_manifest(),
    )

    rendered = str(decision.public_projection())
    assert decision.status == "blocked"
    assert decision.reason_codes == ("mcp_provider_list_failed",)
    assert "/Users/kevin" not in rendered
    assert "unsafe-token" not in rendered
    assert "boom" not in rendered


def test_mcp_list_prompts_auth_error_is_auth_required() -> None:
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    decision = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).list_prompts(
        "mcp:notes",
        provider=FakeMcpProvider(auth_fail=True),
        security_manifest=_security_manifest(),
    )

    rendered = str(decision.public_projection())
    assert decision.status == "auth_required"
    assert decision.reason_codes == ("mcp_auth_required",)
    assert "Authorization" not in rendered
    assert "unsafe-token" not in rendered


# ---------------------------------------------------------------------------
# Prompt resolution (P2 security) — mirrors call_tool gating + redaction
# ---------------------------------------------------------------------------


def test_mcp_resolve_prompt_default_off_does_not_call_provider() -> None:
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    provider = FakeMcpProvider()
    decision = McpAdapter(McpAdapterConfig()).resolve_prompt(
        "mcp:notes", "summarize_note", {}, provider=provider
    )

    assert decision.status == "disabled"
    assert decision.reason_codes == ("mcp_adapter_disabled",)
    assert decision.text == ""
    assert provider.get_prompt_calls == 0
    assert set(decision.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_mcp_resolve_prompt_requires_manifest_before_provider() -> None:
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    provider = FakeMcpProvider()
    decision = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).resolve_prompt("mcp:notes", "summarize_note", {}, provider=provider)

    assert decision.status == "blocked"
    assert decision.reason_codes == ("mcp_security_manifest_required",)
    assert decision.text == ""
    assert provider.get_prompt_calls == 0


def test_mcp_resolve_prompt_blocks_when_no_provider() -> None:
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    decision = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).resolve_prompt(
        "mcp:notes", "summarize_note", {}, provider=None, security_manifest=_security_manifest()
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == ("local_fake_mcp_provider_required",)
    assert decision.text == ""


def test_mcp_resolve_prompt_blocks_untrusted_fake_provider() -> None:
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    provider = UntrustedMcpProvider()
    decision = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).resolve_prompt(
        "mcp:notes", "summarize_note", {}, provider=provider, security_manifest=_security_manifest()
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == ("local_fake_mcp_provider_untrusted",)
    assert decision.text == ""
    assert provider.get_prompt_calls == 0


def test_mcp_resolve_prompt_redacts_secret_body_at_the_seam() -> None:
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    provider = FakeMcpProvider()
    decision = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).resolve_prompt(
        "mcp:notes", "summarize_note", {}, provider=provider, security_manifest=_security_manifest()
    )

    rendered = str({"text": decision.text, "projection": decision.public_projection()})
    assert decision.status == "ok"
    assert provider.get_prompt_calls == 1
    assert "public prompt body" in decision.text
    assert "/Users/kevin" not in rendered
    assert "/private/var" not in rendered
    assert "PRIVATE KEY" not in rendered
    assert "SUPERSECRETKEYBODY" not in rendered
    # public_projection emits only a digest of the body, never the raw text.
    assert decision.public_projection()["textDigest"].startswith("prompt:")
    assert set(decision.public_projection()["authorityFlags"].values()) == {False}


def test_mcp_resolve_prompt_provider_error_returns_safe_digest() -> None:
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    class BoomProvider(FakeMcpProvider):
        def get_prompt(self, server_ref: str, prompt_name: str, arguments: object) -> dict[str, object]:
            self.get_prompt_calls += 1
            raise RuntimeError("boom /Users/kevin/private Authorization: Bearer unsafe-token")

    decision = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).resolve_prompt(
        "mcp:notes", "summarize_note", {}, provider=BoomProvider(), security_manifest=_security_manifest()
    )

    rendered = str({"text": decision.text, "projection": decision.public_projection()})
    assert decision.status == "blocked"
    assert decision.reason_codes == ("mcp_provider_get_prompt_failed",)
    assert decision.text == ""
    assert "/Users/kevin" not in rendered
    assert "unsafe-token" not in rendered
    assert "boom" not in rendered


def test_mcp_resolve_prompt_auth_error_is_auth_required() -> None:
    from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

    class AuthProvider(FakeMcpProvider):
        def get_prompt(self, server_ref: str, prompt_name: str, arguments: object) -> dict[str, object]:
            self.get_prompt_calls += 1
            from magi_agent.plugins.mcp_adapter import McpAuthError

            raise McpAuthError("Authorization: Bearer unsafe-token")

    decision = McpAdapter(
        McpAdapterConfig(enabled=True, localFakeProviderEnabled=True),
    ).resolve_prompt(
        "mcp:notes", "summarize_note", {}, provider=AuthProvider(), security_manifest=_security_manifest()
    )

    rendered = str(decision.public_projection())
    assert decision.status == "auth_required"
    assert decision.reason_codes == ("mcp_auth_required",)
    assert decision.text == ""
    assert "Authorization" not in rendered
    assert "unsafe-token" not in rendered


def test_mcp_prompt_resolve_decision_ignores_forged_authority_flags() -> None:
    from magi_agent.plugins.mcp_adapter import McpPromptResolveDecision

    forged = McpPromptResolveDecision.model_construct(
        status="ok",
        text="",
        reasonCodes=(),
        authorityFlags={"mcpServerAttached": True, "liveToolExecutionEnabled": True},
    )

    assert set(forged.public_projection()["authorityFlags"].values()) == {False}


def test_mcp_prompt_list_decision_ignores_forged_authority_flags() -> None:
    from magi_agent.plugins.mcp_adapter import McpPromptListDecision

    forged = McpPromptListDecision.model_construct(
        status="ok",
        descriptors=(),
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

importlib.import_module("magi_agent.plugins.mcp_adapter")
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
    "magi_agent.runtime.route_activation",
    "magi_agent.runtime.adk_turn_runner",
    "magi_agent.runtime.model_routing",
    "magi_agent.transport.chat",
    "magi_agent.transport.chat_route",
    "magi_agent.tools.kernel",
    "magi_agent.tools.dispatcher",
    "magi_agent.deploy",
    "magi_agent.provisioning",
)
forbidden_prefixes = (
    "magi_agent.adk_bridge.",
    "magi_agent.deploy.",
    "magi_agent.provisioning.",
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
