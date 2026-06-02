from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from openmagi_core_agent.tools.context import ToolContext


def _context(
    workspace_root: Path,
    *,
    session_id: str = "session-pr3",
    workspace_ref: str | None = None,
) -> ToolContext:
    return ToolContext(
        botId="bot-pr3",
        userId="user-pr3",
        sessionId=session_id,
        sessionKey="context-ref-pr3",
        turnId="turn-pr3",
        toolUseId="toolu-pr3",
        workspaceRoot=str(workspace_root),
        workspaceRef=workspace_ref,
    )


def test_reference_research_tools_default_off_and_no_handler_call(tmp_path: Path) -> None:
    from openmagi_core_agent.web_acquisition.reference_research_tools import (
        ManagedReferenceCache,
        ReferenceAwareResearchToolBoundary,
    )

    cache = ManagedReferenceCache()
    result = asyncio.run(
        ReferenceAwareResearchToolBoundary(reference_cache=cache).execute_tool(
            "Read",
            {"ref": "managed-ref:missing"},
            _context(tmp_path),
        )
    )
    encoded = json.dumps(result.model_dump(by_alias=True, mode="python"), sort_keys=True)

    assert result.status == "disabled"
    assert result.error_code == "reference_adapter_disabled"
    assert cache.host_call_log == ()
    assert "managed-ref:missing" not in encoded
    assert "liveToolDispatched" in encoded
    assert "true" not in encoded.lower()


def test_reference_config_construct_cannot_enable_live_authority() -> None:
    from openmagi_core_agent.web_acquisition.reference_research_tools import (
        ReferenceResearchConfig,
    )

    config = ReferenceResearchConfig.model_construct(
        productionNetworkEnabled=True,
        liveAuthorityAllowed=True,
        liveToolExecutionEnabled=True,
        modelCallEnabled=True,
        browserExecutionEnabled=True,
        channelDeliveryEnabled=True,
        memoryWriteEnabled=True,
        workspaceMutationEnabled=True,
    )
    projection = config.model_dump(by_alias=True, mode="python")

    assert projection["productionNetworkEnabled"] is False
    assert projection["liveAuthorityAllowed"] is False
    assert projection["liveToolExecutionEnabled"] is False
    assert projection["modelCallEnabled"] is False
    assert projection["browserExecutionEnabled"] is False
    assert projection["channelDeliveryEnabled"] is False
    assert projection["memoryWriteEnabled"] is False
    assert projection["workspaceMutationEnabled"] is False


def test_reference_read_uses_toolhost_read_ledger_and_projects_digest_safe_output(
    tmp_path: Path,
) -> None:
    from openmagi_core_agent.web_acquisition.reference_research_tools import (
        ManagedReferenceCache,
        ReferenceAwareResearchToolBoundary,
        ReferenceResearchConfig,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "alpha\nAuthorization: Bearer unsafe-token\n",
        encoding="utf-8",
    )
    context = _context(tmp_path)
    cache = ManagedReferenceCache()
    managed_ref = cache.issue_path_reference(context, path="src/app.py")
    boundary = ReferenceAwareResearchToolBoundary(
        config=ReferenceResearchConfig(enabled=True, localFakeProviderEnabled=True),
        reference_cache=cache,
    )

    result = asyncio.run(
        boundary.execute_tool(
            "Read",
            {"ref": managed_ref.ref, "maxBytes": 256},
            context,
        )
    )
    encoded = json.dumps(result.model_dump(by_alias=True, mode="python"), sort_keys=True)

    assert result.status == "ok"
    assert result.output["toolName"] == "Read"
    assert result.output["underlyingTool"] == "FileRead"
    assert result.output["managedReference"]["ref"] == managed_ref.ref
    assert result.output["managedReference"]["pathRef"].startswith("path-ref:")
    assert result.output["readLedgerDecision"]["status"] == "ok"
    assert result.output["readLedgerDecision"]["reasonCodes"] == ["fresh_full_read"]
    assert result.output["toolHost"]["status"] == "ok"
    assert result.output["toolHost"]["sourceRefs"] == ["src_1"]
    assert boundary.host_call_log == ("FileRead",)
    assert "unsafe-token" not in encoded
    assert "Authorization" not in encoded
    assert str(tmp_path) not in encoded
    assert "/Users/" not in encoded
    assert result.metadata["authorityFlags"]["liveToolDispatched"] is False
    assert result.metadata["authorityFlags"]["workspaceMutationAllowed"] is False


def test_reference_tools_reject_unissued_ref_before_toolhost_call(tmp_path: Path) -> None:
    from openmagi_core_agent.web_acquisition.reference_research_tools import (
        ManagedReferenceCache,
        ReferenceAwareResearchToolBoundary,
        ReferenceResearchConfig,
    )

    cache = ManagedReferenceCache()
    boundary = ReferenceAwareResearchToolBoundary(
        config=ReferenceResearchConfig(enabled=True, localFakeProviderEnabled=True),
        reference_cache=cache,
    )
    result = asyncio.run(
        boundary.execute_tool("Read", {"ref": "managed-ref:forged"}, _context(tmp_path))
    )
    encoded = json.dumps(result.model_dump(by_alias=True, mode="python"), sort_keys=True)

    assert result.status == "blocked"
    assert result.error_code == "managed_ref_unissued"
    assert boundary.host_call_log == ()
    assert "managed-ref:forged" not in encoded


def test_reference_tools_reject_stale_ref_before_toolhost_call(tmp_path: Path) -> None:
    from openmagi_core_agent.web_acquisition.reference_research_tools import (
        ManagedReferenceCache,
        ReferenceAwareResearchToolBoundary,
        ReferenceResearchConfig,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("alpha\n", encoding="utf-8")
    context = _context(tmp_path)
    cache = ManagedReferenceCache()
    managed_ref = cache.issue_path_reference(context, path="src/app.py")
    (tmp_path / "src" / "app.py").write_text("changed\n", encoding="utf-8")
    boundary = ReferenceAwareResearchToolBoundary(
        config=ReferenceResearchConfig(enabled=True, localFakeProviderEnabled=True),
        reference_cache=cache,
    )

    result = asyncio.run(boundary.execute_tool("Read", {"ref": managed_ref.ref}, context))

    assert result.status == "blocked"
    assert result.error_code == "managed_ref_stale"
    assert boundary.host_call_log == ()


def test_reference_tools_reject_unscoped_ref_before_toolhost_call(tmp_path: Path) -> None:
    from openmagi_core_agent.web_acquisition.reference_research_tools import (
        ManagedReferenceCache,
        ReferenceAwareResearchToolBoundary,
        ReferenceResearchConfig,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("alpha\n", encoding="utf-8")
    cache = ManagedReferenceCache()
    managed_ref = cache.issue_path_reference(_context(tmp_path), path="src/app.py")
    boundary = ReferenceAwareResearchToolBoundary(
        config=ReferenceResearchConfig(enabled=True, localFakeProviderEnabled=True),
        reference_cache=cache,
    )

    result = asyncio.run(
        boundary.execute_tool(
            "Read",
            {"ref": managed_ref.ref},
            _context(tmp_path, session_id="other-session"),
        )
    )

    assert result.status == "blocked"
    assert result.error_code == "managed_ref_scope_mismatch"
    assert boundary.host_call_log == ()


def test_reference_tools_reject_same_workspace_ref_on_different_root(
    tmp_path: Path,
) -> None:
    from openmagi_core_agent.web_acquisition.reference_research_tools import (
        ManagedReferenceCache,
        ReferenceAwareResearchToolBoundary,
        ReferenceResearchConfig,
    )

    root_a = tmp_path / "workspace-a"
    root_b = tmp_path / "workspace-b"
    (root_a / "src").mkdir(parents=True)
    (root_b / "src").mkdir(parents=True)
    (root_a / "src" / "app.py").write_text("alpha\n", encoding="utf-8")
    (root_b / "src" / "app.py").write_text("alpha\n", encoding="utf-8")
    cache = ManagedReferenceCache()
    managed_ref = cache.issue_path_reference(
        _context(root_a, workspace_ref="workspace-ref:shared"),
        path="src/app.py",
    )
    boundary = ReferenceAwareResearchToolBoundary(
        config=ReferenceResearchConfig(enabled=True, localFakeProviderEnabled=True),
        reference_cache=cache,
    )

    result = asyncio.run(
        boundary.execute_tool(
            "Read",
            {"ref": managed_ref.ref},
            _context(root_b, workspace_ref="workspace-ref:shared"),
        )
    )

    assert result.status == "blocked"
    assert result.error_code == "managed_ref_scope_mismatch"
    assert boundary.host_call_log == ()


@pytest.mark.parametrize(
    "unsafe_path",
    [
        ".env",
        "../outside.py",
        "/Users/kevin/.ssh/id_rsa",
        "AGENTS.md",
        "SCRATCHPAD.md",
        "WORKING.md",
        "TASK-QUEUE.md",
        "src/config.py",
        "src/auth.json",
        "src/keys.json",
        "src/cookie-cache.txt",
    ],
)
def test_reference_cache_rejects_toolhost_denied_private_or_protected_paths(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    from openmagi_core_agent.web_acquisition.reference_research_tools import (
        ManagedReferenceCache,
    )

    (tmp_path / ".env").write_text("SECRET=value\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("sealed\n", encoding="utf-8")
    (tmp_path / "SCRATCHPAD.md").write_text("ops\n", encoding="utf-8")
    (tmp_path / "WORKING.md").write_text("ops\n", encoding="utf-8")
    (tmp_path / "TASK-QUEUE.md").write_text("ops\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "config.py").write_text("setting = 'private'\n", encoding="utf-8")
    (tmp_path / "src" / "auth.json").write_text('{"token": "private"}\n', encoding="utf-8")
    (tmp_path / "src" / "keys.json").write_text('{"key": "private"}\n', encoding="utf-8")
    (tmp_path / "src" / "cookie-cache.txt").write_text("cookie=private\n", encoding="utf-8")
    cache = ManagedReferenceCache()

    with pytest.raises(ValueError, match="unsafe managed reference path"):
        cache.issue_path_reference(_context(tmp_path), path=unsafe_path)


def test_reference_directory_digest_skips_toolhost_denied_nested_paths(
    tmp_path: Path,
) -> None:
    from openmagi_core_agent.web_acquisition.reference_research_tools import (
        ManagedReferenceCache,
        ReferenceAwareResearchToolBoundary,
        ReferenceResearchConfig,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "safe.py").write_text("alpha\n", encoding="utf-8")
    (tmp_path / "src" / "config.py").write_text("secret-alpha\n", encoding="utf-8")
    context = _context(tmp_path)
    cache = ManagedReferenceCache()
    managed_ref = cache.issue_path_reference(context, path="src")
    boundary = ReferenceAwareResearchToolBoundary(
        config=ReferenceResearchConfig(enabled=True, localFakeProviderEnabled=True),
        reference_cache=cache,
    )

    result = asyncio.run(
        boundary.execute_tool(
            "Grep",
            {"ref": managed_ref.ref, "pattern": "secret-alpha", "glob": "**/*"},
            context,
        )
    )
    encoded = json.dumps(result.model_dump(by_alias=True, mode="python"), sort_keys=True)

    assert result.status == "ok"
    assert result.output["toolHost"]["matchCount"] == 0
    assert boundary.host_call_log == ("Grep",)
    assert "config.py" not in encoded
    assert "secret-alpha" not in encoded


def test_reference_grep_and_glob_route_through_toolhost_without_private_paths(
    tmp_path: Path,
) -> None:
    from openmagi_core_agent.web_acquisition.reference_research_tools import (
        ManagedReferenceCache,
        ReferenceAwareResearchToolBoundary,
        ReferenceResearchConfig,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("alpha\n", encoding="utf-8")
    (tmp_path / "src" / "notes.md").write_text("beta\n", encoding="utf-8")
    context = _context(tmp_path)
    cache = ManagedReferenceCache()
    managed_ref = cache.issue_path_reference(context, path="src")
    boundary = ReferenceAwareResearchToolBoundary(
        config=ReferenceResearchConfig(enabled=True, localFakeProviderEnabled=True),
        reference_cache=cache,
    )

    grep_result = asyncio.run(
        boundary.execute_tool(
            "Grep",
            {"ref": managed_ref.ref, "pattern": "alpha", "glob": "*.py"},
            context,
        )
    )
    glob_result = asyncio.run(
        boundary.execute_tool(
            "Glob",
            {"ref": managed_ref.ref, "pattern": "*.py"},
            context,
        )
    )
    encoded = json.dumps(
        {
            "grep": grep_result.model_dump(by_alias=True, mode="python"),
            "glob": glob_result.model_dump(by_alias=True, mode="python"),
        },
        sort_keys=True,
    )

    assert grep_result.status == "ok"
    assert grep_result.output["underlyingTool"] == "Grep"
    assert grep_result.output["toolHost"]["matchCount"] == 1
    assert glob_result.status == "ok"
    assert glob_result.output["underlyingTool"] == "Glob"
    assert glob_result.output["toolHost"]["matchCount"] == 1
    assert boundary.host_call_log == ("Grep", "Glob")
    assert str(tmp_path) not in encoded
    assert "/Users/" not in encoded


@pytest.mark.parametrize("unsafe_pattern", ["../secret.py", "AGENTS.md"])
def test_reference_tools_block_unsafe_glob_before_toolhost_call(
    tmp_path: Path,
    unsafe_pattern: str,
) -> None:
    from openmagi_core_agent.web_acquisition.reference_research_tools import (
        ManagedReferenceCache,
        ReferenceAwareResearchToolBoundary,
        ReferenceResearchConfig,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("alpha\n", encoding="utf-8")
    context = _context(tmp_path)
    cache = ManagedReferenceCache()
    managed_ref = cache.issue_path_reference(context, path="src")
    boundary = ReferenceAwareResearchToolBoundary(
        config=ReferenceResearchConfig(enabled=True, localFakeProviderEnabled=True),
        reference_cache=cache,
    )

    result = asyncio.run(
        boundary.execute_tool(
            "Glob",
            {"ref": managed_ref.ref, "pattern": unsafe_pattern},
            context,
        )
    )
    encoded = json.dumps(result.model_dump(by_alias=True, mode="python"), sort_keys=True)

    assert result.status == "blocked"
    assert result.error_code == "unsafe_managed_reference_pattern"
    assert boundary.host_call_log == ()
    assert unsafe_pattern not in encoded


def test_reference_directory_stale_blocks_search_before_toolhost_call(
    tmp_path: Path,
) -> None:
    from openmagi_core_agent.web_acquisition.reference_research_tools import (
        ManagedReferenceCache,
        ReferenceAwareResearchToolBoundary,
        ReferenceResearchConfig,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("alpha\n", encoding="utf-8")
    context = _context(tmp_path)
    cache = ManagedReferenceCache()
    managed_ref = cache.issue_path_reference(context, path="src")
    (tmp_path / "src" / "app.py").write_text("changed\n", encoding="utf-8")
    boundary = ReferenceAwareResearchToolBoundary(
        config=ReferenceResearchConfig(enabled=True, localFakeProviderEnabled=True),
        reference_cache=cache,
    )

    result = asyncio.run(
        boundary.execute_tool(
            "Grep",
            {"ref": managed_ref.ref, "pattern": "changed", "glob": "*.py"},
            context,
        )
    )

    assert result.status == "blocked"
    assert result.error_code == "managed_ref_stale"
    assert boundary.host_call_log == ()


def test_reference_tools_do_not_echo_raw_private_forged_ref(
    tmp_path: Path,
) -> None:
    from openmagi_core_agent.web_acquisition.reference_research_tools import (
        ManagedReferenceCache,
        ReferenceAwareResearchToolBoundary,
        ReferenceResearchConfig,
    )

    cache = ManagedReferenceCache()
    boundary = ReferenceAwareResearchToolBoundary(
        config=ReferenceResearchConfig(enabled=True, localFakeProviderEnabled=True),
        reference_cache=cache,
    )

    result = asyncio.run(
        boundary.execute_tool(
            "Read",
            {"ref": "/Users/kevin/.ssh/id_rsa?token=unsafe"},
            _context(tmp_path),
        )
    )
    encoded = json.dumps(result.model_dump(by_alias=True, mode="python"), sort_keys=True)

    assert result.status == "blocked"
    assert result.error_code == "managed_ref_unissued"
    assert boundary.host_call_log == ()
    assert "/Users/kevin" not in encoded
    assert "unsafe" not in encoded


def test_reference_grep_preserves_toolhost_handler_error_code(
    tmp_path: Path,
) -> None:
    from openmagi_core_agent.web_acquisition.reference_research_tools import (
        ManagedReferenceCache,
        ReferenceAwareResearchToolBoundary,
        ReferenceResearchConfig,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "safe.py").write_text("alpha\n", encoding="utf-8")
    context = _context(tmp_path)
    cache = ManagedReferenceCache()
    managed_ref = cache.issue_path_reference(context, path="src")
    boundary = ReferenceAwareResearchToolBoundary(
        config=ReferenceResearchConfig(enabled=True, localFakeProviderEnabled=True),
        reference_cache=cache,
    )

    result = asyncio.run(
        boundary.execute_tool(
            "Grep",
            {"ref": managed_ref.ref, "pattern": "["},
            context,
        )
    )

    assert result.status == "blocked"
    assert result.error_code == "grep_pattern_invalid"
    assert boundary.host_call_log == ("Grep",)
