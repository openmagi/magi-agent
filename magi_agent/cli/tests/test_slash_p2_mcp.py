"""Tests for P2 — MCP prompts projected as CLI slash-commands.

Covers:
- ``McpPromptCommand.build_prompt``: $1..$N argument mapping, missing args,
  resolver invoked with the correct name→value mapping, template returned as a
  single ``ContentBlock``.
- ``mcp_prompt_commands``: default-off returns ``[]``; with a local-fake
  provider returns commands carrying ``source == "mcp"`` and correct ``hints``;
  prompt text extracted from the ``prompts/get`` result.
- discovery: injected MCP commands appear in the ``plugin`` tier; a project
  ``.claude/commands/<name>.md`` shadows a same-named MCP command.

Plain pytest + asyncio.run — no pytest-asyncio, matching existing convention.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from magi_agent.cli.commands.discovery import (
    MarkdownPromptCommand,
    discover_commands,
)
from magi_agent.cli.commands.mcp_commands import (
    McpPromptCommand,
    mcp_prompt_commands,
)
from magi_agent.cli.contracts import (
    CommandContext,
    CommandSurface,
    ContentBlock,
)
from magi_agent.plugins.mcp_adapter import McpAdapter, McpAdapterConfig

BOTH = CommandSurface(tui=True, headless=True)


def _ctx(cwd: str = "/tmp/test-cwd") -> CommandContext:
    return CommandContext(cwd=cwd)


def _security_manifest() -> dict[str, object]:
    return {
        "serverRef": "mcp:notes",
        "trustLevel": "local_dev",
        "sandboxMode": "in_process_contract_only",
        "allowedPermissions": ("read", "write"),
        "supplyChainDigest": "sha256:" + "a" * 64,
    }


class FakePromptProvider:
    """Local-fake MCP provider exposing prompts only (tools unused here)."""

    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.get_prompt_calls: list[tuple[str, str, dict]] = []

    def list_tools(self, server_ref: str):  # pragma: no cover - unused in P2 tests
        return []

    def call_tool(self, server_ref: str, tool_name: str, arguments):  # pragma: no cover
        return {}

    def list_prompts(self, server_ref: str) -> list[dict[str, object]]:
        return [
            {
                "name": "summarize_note",
                "description": "Summarize a selected note",
                "arguments": [
                    {"name": "noteName"},
                    {"name": "tone"},
                ],
            },
            {
                "name": "no_args_prompt",
                "description": "No arguments",
                "arguments": [],
            },
        ]

    def get_prompt(self, server_ref: str, prompt_name: str, arguments) -> dict[str, object]:
        self.get_prompt_calls.append((server_ref, prompt_name, dict(arguments)))
        return {
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": f"Resolved {prompt_name} with {dict(arguments)}",
                    },
                }
            ],
        }


class UntrustedPromptProvider(FakePromptProvider):
    openmagi_local_fake_provider = False


# ===========================================================================
# McpPromptCommand.build_prompt
# ===========================================================================


class TestMcpPromptCommandBuildPrompt:
    def _cmd(self, captured: dict) -> McpPromptCommand:
        def _resolver(mapping):
            captured["mapping"] = dict(mapping)
            return f"template[{mapping.get('noteName', '')}|{mapping.get('tone', '')}]"

        return McpPromptCommand(
            name="mcp.notes.summarize_note",
            surface=BOTH,
            description="d",
            argument_names=("noteName", "tone"),
            resolver=_resolver,
            source="mcp",
            hints=["$1", "$2"],
        )

    def test_positional_args_mapped_to_names(self) -> None:
        captured: dict = {}
        cmd = self._cmd(captured)
        asyncio.run(cmd.build_prompt("alpha beta", _ctx()))
        assert captured["mapping"] == {"noteName": "alpha", "tone": "beta"}

    def test_returns_template_as_single_content_block(self) -> None:
        captured: dict = {}
        cmd = self._cmd(captured)
        blocks = asyncio.run(cmd.build_prompt("alpha beta", _ctx()))
        assert len(blocks) == 1
        assert isinstance(blocks[0], ContentBlock)
        assert blocks[0].text == "template[alpha|beta]"

    def test_missing_positional_maps_to_empty(self) -> None:
        captured: dict = {}
        cmd = self._cmd(captured)
        asyncio.run(cmd.build_prompt("alpha", _ctx()))
        assert captured["mapping"] == {"noteName": "alpha", "tone": ""}

    def test_none_args_maps_all_empty(self) -> None:
        captured: dict = {}
        cmd = self._cmd(captured)
        asyncio.run(cmd.build_prompt(None, _ctx()))
        assert captured["mapping"] == {"noteName": "", "tone": ""}

    def test_extra_tokens_ignored(self) -> None:
        captured: dict = {}
        cmd = self._cmd(captured)
        asyncio.run(cmd.build_prompt("a b c d", _ctx()))
        assert captured["mapping"] == {"noteName": "a", "tone": "b"}

    def test_no_resolver_returns_empty_text(self) -> None:
        cmd = McpPromptCommand(
            name="mcp.notes.x",
            surface=BOTH,
            argument_names=(),
            resolver=None,
            source="mcp",
        )
        blocks = asyncio.run(cmd.build_prompt(None, _ctx()))
        assert blocks[0].text == ""

    def test_source_marker_is_mcp(self) -> None:
        cmd = self._cmd({})
        assert cmd.source == "mcp"


# ===========================================================================
# mcp_prompt_commands
# ===========================================================================


class TestMcpPromptCommands:
    def test_default_off_no_provider_returns_empty(self) -> None:
        adapter = McpAdapter(McpAdapterConfig(enabled=True, localFakeProviderEnabled=True))
        result = mcp_prompt_commands(adapter, None, ["mcp:notes"], {"mcp:notes": _security_manifest()})
        assert result == []

    def test_disabled_adapter_returns_empty(self) -> None:
        adapter = McpAdapter(McpAdapterConfig())  # disabled
        result = mcp_prompt_commands(
            adapter,
            FakePromptProvider(),
            ["mcp:notes"],
            {"mcp:notes": _security_manifest()},
        )
        assert result == []

    def test_blocked_untrusted_provider_returns_empty(self) -> None:
        adapter = McpAdapter(McpAdapterConfig(enabled=True, localFakeProviderEnabled=True))
        result = mcp_prompt_commands(
            adapter,
            UntrustedPromptProvider(),
            ["mcp:notes"],
            {"mcp:notes": _security_manifest()},
        )
        assert result == []

    def test_missing_manifest_returns_empty(self) -> None:
        adapter = McpAdapter(McpAdapterConfig(enabled=True, localFakeProviderEnabled=True))
        # No manifest supplied for the server → adapter blocks → no commands.
        result = mcp_prompt_commands(adapter, FakePromptProvider(), ["mcp:notes"], None)
        assert result == []

    def test_happy_path_builds_commands(self) -> None:
        adapter = McpAdapter(McpAdapterConfig(enabled=True, localFakeProviderEnabled=True))
        result = mcp_prompt_commands(
            adapter,
            FakePromptProvider(),
            ["mcp:notes"],
            {"mcp:notes": _security_manifest()},
        )
        names = [c.name for c in result]
        assert names == ["mcp.notes.summarize_note", "mcp.notes.no_args_prompt"]
        assert all(isinstance(c, McpPromptCommand) for c in result)
        assert all(c.source == "mcp" for c in result)

    def test_hints_computed_from_argument_count(self) -> None:
        adapter = McpAdapter(McpAdapterConfig(enabled=True, localFakeProviderEnabled=True))
        result = mcp_prompt_commands(
            adapter,
            FakePromptProvider(),
            ["mcp:notes"],
            {"mcp:notes": _security_manifest()},
        )
        by_name = {c.name: c for c in result}
        assert by_name["mcp.notes.summarize_note"].hints == ["$1", "$2"]
        assert by_name["mcp.notes.no_args_prompt"].hints == []

    def test_resolver_invokes_provider_get_prompt_and_extracts_text(self) -> None:
        provider = FakePromptProvider()
        adapter = McpAdapter(McpAdapterConfig(enabled=True, localFakeProviderEnabled=True))
        result = mcp_prompt_commands(
            adapter,
            provider,
            ["mcp:notes"],
            {"mcp:notes": _security_manifest()},
        )
        cmd = next(c for c in result if c.name == "mcp.notes.summarize_note")
        # Argument names are redaction-scrubbed by the adapter (``_safe_tool_segment``
        # lowercases), so ``noteName`` is projected as ``notename``. The resolver
        # passes the descriptor's (safe) names through to the provider.
        assert cmd.argument_names == ("notename", "tone")
        blocks = asyncio.run(cmd.build_prompt("alpha beta", _ctx()))
        # provider.get_prompt was called with the leaf name + mapped arguments.
        assert provider.get_prompt_calls == [
            ("mcp:notes", "summarize_note", {"notename": "alpha", "tone": "beta"})
        ]
        assert blocks[0].text.startswith("Resolved summarize_note")
        assert "alpha" in blocks[0].text

    def test_surface_both(self) -> None:
        adapter = McpAdapter(McpAdapterConfig(enabled=True, localFakeProviderEnabled=True))
        result = mcp_prompt_commands(
            adapter,
            FakePromptProvider(),
            ["mcp:notes"],
            {"mcp:notes": _security_manifest()},
        )
        for cmd in result:
            assert cmd.surface.tui is True
            assert cmd.surface.headless is True


# ===========================================================================
# discovery integration (plugin tier + shadowing)
# ===========================================================================


class TestDiscoveryIntegration:
    def _mcp_cmds(self):
        adapter = McpAdapter(McpAdapterConfig(enabled=True, localFakeProviderEnabled=True))
        return mcp_prompt_commands(
            adapter,
            FakePromptProvider(),
            ["mcp:notes"],
            {"mcp:notes": _security_manifest()},
        )

    def test_injected_mcp_commands_appear_in_discovery(self) -> None:
        mcp_cmds = self._mcp_cmds()
        assert mcp_cmds  # sanity
        discovered = discover_commands("/tmp/no-such-cwd", mcp_commands=mcp_cmds)
        names = {c.name for c in discovered}
        assert "mcp.notes.summarize_note" in names
        assert "mcp.notes.no_args_prompt" in names

    def test_default_off_discovery_has_no_mcp_commands(self) -> None:
        discovered = discover_commands("/tmp/no-such-cwd")
        names = {c.name for c in discovered}
        assert not any(n.startswith("mcp.notes.") for n in names)

    def test_mcp_command_in_plugin_tier_not_higher(self) -> None:
        """MCP commands land at the plugin tier (5), below skill_dir (3)."""
        mcp_cmds = self._mcp_cmds()
        discovered = discover_commands("/tmp/no-such-cwd", mcp_commands=mcp_cmds)
        cmd = next(c for c in discovered if c.name == "mcp.notes.summarize_note")
        assert isinstance(cmd, McpPromptCommand)

    def test_project_markdown_shadows_same_named_mcp_command(self, tmp_path: Path) -> None:
        """A .claude/commands/<name>.md (skill_dir tier 3) shadows a same-named
        MCP command (plugin tier 5)."""
        commands_dir = tmp_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        # Project command with the SAME name as an MCP prompt command.
        (commands_dir / "mcp.notes.summarize_note.md").write_text(
            "project override body",
            encoding="utf-8",
        )

        mcp_cmds = self._mcp_cmds()
        discovered = discover_commands(str(tmp_path), mcp_commands=mcp_cmds)
        by_name = {c.name: c for c in discovered}
        assert "mcp.notes.summarize_note" in by_name
        cmd = by_name["mcp.notes.summarize_note"]
        assert isinstance(cmd, MarkdownPromptCommand), (
            "Project .claude/commands markdown (tier 3) must shadow the MCP "
            f"command (tier 5); got {type(cmd).__name__}"
        )
        # The other MCP command (no project shadow) is still the MCP one.
        assert isinstance(by_name["mcp.notes.no_args_prompt"], McpPromptCommand)
