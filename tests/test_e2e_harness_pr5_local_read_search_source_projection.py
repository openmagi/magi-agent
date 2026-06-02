from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from openmagi_core_agent.tools.catalog import register_core_tool_manifests
from openmagi_core_agent.tools.context import ToolContext
from openmagi_core_agent.tools.kernel import (
    ToolExecutionKernel,
    ToolExecutionKernelConfig,
    ToolExecutionRequest,
)
from openmagi_core_agent.tools.registry import ToolRegistry


def _context(workspace_root: Path) -> ToolContext:
    return ToolContext(
        botId="bot-pr5",
        userId="user-pr5",
        sessionId="session-pr5",
        sessionKey="context-ref-pr5",
        turnId="turn-pr5",
        workspaceRoot=str(workspace_root),
    )


def _registry(*enabled: str) -> ToolRegistry:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)
    for name in enabled:
        registry.enable(name)
    return registry


def _execute(
    *,
    workspace_root: Path,
    tool_name: str,
    arguments: dict[str, object],
    enabled_tools: tuple[str, ...] = ("FileRead", "Glob", "Grep", "GitDiff"),
    exposed_tool_names: tuple[str, ...] | None = ("FileRead", "Glob", "Grep", "GitDiff"),
    host: object | None = None,
) -> object:
    from openmagi_core_agent.tools.local_readonly import LocalReadOnlyToolHost

    registry = _registry(*enabled_tools)
    safe_host = host or LocalReadOnlyToolHost()
    return asyncio.run(
        ToolExecutionKernel(
            registry,
            config=ToolExecutionKernelConfig(
                enabled=True,
                localFakeHandlerExecutionEnabled=True,
                outputBudgetEnabled=True,
            ),
            local_fake_executor=safe_host,
        ).execute(
            ToolExecutionRequest(
                toolName=tool_name,
                arguments=arguments,
                context=_context(workspace_root),
                mode="act",
                exposedToolNames=exposed_tool_names,
                toolCallId=f"call-pr5-{tool_name}",
            )
        )
    )


def test_pr5_local_readonly_tools_remain_default_off_until_registry_and_kernel_enable(
    tmp_path: Path,
) -> None:
    from openmagi_core_agent.tools.local_readonly import LocalReadOnlyToolHost

    (tmp_path / "notes.txt").write_text("visible\n", encoding="utf-8")
    registry = _registry()
    host = LocalReadOnlyToolHost()

    outcome = asyncio.run(
        ToolExecutionKernel(
            registry,
            config=ToolExecutionKernelConfig(
                enabled=True,
                localFakeHandlerExecutionEnabled=True,
            ),
            local_fake_executor=host,
        ).execute(
            ToolExecutionRequest(
                toolName="FileRead",
                arguments={"path": "notes.txt"},
                context=_context(tmp_path),
                mode="act",
                exposedToolNames=("FileRead",),
            )
        )
    )

    assert outcome.status == "blocked"
    assert outcome.reason_code == "tool_disabled"
    assert outcome.handler_called is False
    assert host.call_log == ()


def test_pr5_toolhost_allowlist_blocks_hidden_enabled_tool_before_local_read(
    tmp_path: Path,
) -> None:
    from openmagi_core_agent.tools.local_readonly import LocalReadOnlyToolHost

    (tmp_path / "notes.txt").write_text("visible\n", encoding="utf-8")
    registry = _registry("FileRead", "Glob")
    host = LocalReadOnlyToolHost()

    outcome = asyncio.run(
        ToolExecutionKernel(
            registry,
            config=ToolExecutionKernelConfig(
                enabled=True,
                localFakeHandlerExecutionEnabled=True,
            ),
            local_fake_executor=host,
        ).execute(
            ToolExecutionRequest(
                toolName="FileRead",
                arguments={"path": "notes.txt"},
                context=_context(tmp_path),
                mode="act",
                exposedToolNames=("Glob",),
            )
        )
    )

    assert outcome.status == "error"
    assert outcome.reason_code == "tool_not_exposed"
    assert outcome.handler_called is False
    assert host.call_log == ()


def test_pr5_file_read_is_workspace_confined_capped_redacted_and_source_projected(
    tmp_path: Path,
) -> None:
    (tmp_path / "notes.txt").write_text(
        "alpha\nAuthorization: Bearer live-token\n" + ("body-line\n" * 40),
        encoding="utf-8",
    )

    outcome = _execute(
        workspace_root=tmp_path,
        tool_name="FileRead",
        arguments={"path": "notes.txt", "maxBytes": 96},
    )
    dumped = outcome.model_dump_json(by_alias=True)

    assert outcome.status == "ok"
    assert outcome.result.status == "ok"
    assert outcome.result.file_refs == ("src_1",)
    assert outcome.result.output["sourceRef"] == "src_1"
    assert outcome.result.output["path"] == "notes.txt"
    assert outcome.result.output["truncated"] is True
    assert outcome.result.metadata["sourceProjection"]["sources"][0]["uri"] == "[redacted]"
    assert outcome.result.metadata["sourceProjection"]["sources"][0]["scope"]["agentRole"] == "general"
    assert outcome.result.metadata["sourceEvidenceReceipts"][0]["sourceRef"] == "src_1"
    assert outcome.result.metadata["toolExecutionReceipt"]["authorityFlags"] == {
        "readOnly": True,
        "mutationAllowed": False,
        "channelDeliveryAllowed": False,
        "memoryWriteAllowed": False,
    }
    assert outcome.output_projection is not None
    assert outcome.output_projection["digest"].startswith("sha256:")
    assert "live-token" not in dumped
    assert "Authorization" not in dumped
    assert str(tmp_path) not in dumped
    assert "/Users/" not in dumped


def test_pr5_file_read_evidence_result_summary_never_contains_raw_tool_output(
    tmp_path: Path,
) -> None:
    (tmp_path / "notes.txt").write_text(
        "alpha raw workspace content\nsessionKey=unsafe-session-key\n",
        encoding="utf-8",
    )

    outcome = _execute(
        workspace_root=tmp_path,
        tool_name="FileRead",
        arguments={"path": "notes.txt"},
    )
    result_summary = json.dumps(
        outcome.evidence_records[1].result_summary,
        sort_keys=True,
    )

    assert outcome.status == "ok"
    assert "raw workspace content" not in result_summary
    assert "unsafe-session-key" not in result_summary
    assert outcome.evidence_records[1].result_summary["outputPreview"] == "[redacted-output]"
    assert outcome.evidence_records[1].result_summary["llmOutputPreview"] == "[redacted-output]"


def test_pr5_file_read_preserves_real_top_level_a_b_directories(
    tmp_path: Path,
) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "foo.txt").write_text("nested safe\n", encoding="utf-8")
    (tmp_path / "foo.txt").write_text("root should not be read\n", encoding="utf-8")

    outcome = _execute(
        workspace_root=tmp_path,
        tool_name="FileRead",
        arguments={"path": "a/foo.txt"},
    )
    dumped = outcome.model_dump_json(by_alias=True)

    assert outcome.status == "ok"
    assert outcome.result.output["path"] == "a/foo.txt"
    assert "nested safe" in dumped
    assert "root should not be read" not in dumped


def test_pr5_file_read_blocks_traversal_secret_paths_and_symlink_escape(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside-pr5-secret.txt"
    outside.write_text("outside secret\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=live-token\n", encoding="utf-8")
    link = tmp_path / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        link = None

    traversal = _execute(
        workspace_root=tmp_path,
        tool_name="FileRead",
        arguments={"path": "../outside-pr5-secret.txt"},
    )
    secret = _execute(
        workspace_root=tmp_path,
        tool_name="FileRead",
        arguments={"path": ".env"},
    )

    assert traversal.status == "blocked"
    assert traversal.handler_called is False
    assert secret.status == "blocked"
    assert secret.handler_called is False

    if link is not None:
        symlink = _execute(
            workspace_root=tmp_path,
            tool_name="FileRead",
            arguments={"path": "linked.txt"},
        )
        dumped = symlink.model_dump_json(by_alias=True)

        assert symlink.status == "blocked"
        assert symlink.handler_called is True
        assert symlink.result.error_code == "path_symlink_denied"
        assert "outside secret" not in dumped
        assert str(outside) not in dumped


def test_pr5_file_read_blocks_common_plural_and_compound_secret_filenames(
    tmp_path: Path,
) -> None:
    for filename in ("secrets.yaml", "tokens.json", "privatekey.pem", "api_keys.json"):
        (tmp_path / filename).write_text("PRIVATE SECRET MATERIAL\n", encoding="utf-8")

        outcome = _execute(
            workspace_root=tmp_path,
            tool_name="FileRead",
            arguments={"path": filename},
        )
        dumped = outcome.model_dump_json(by_alias=True)

        assert outcome.status == "blocked"
        assert outcome.handler_called is False
        assert "PRIVATE SECRET MATERIAL" not in dumped
        assert filename not in dumped


def test_pr5_file_read_rejects_symlink_to_protected_in_workspace_path(
    tmp_path: Path,
) -> None:
    protected = tmp_path / "SCRATCHPAD.md"
    protected.write_text("PRIVATE SCRATCH SECRET\n", encoding="utf-8")
    link = tmp_path / "public-link.txt"
    try:
        link.symlink_to(protected)
    except OSError:
        pytest.skip("symlinks unavailable on this filesystem")

    outcome = _execute(
        workspace_root=tmp_path,
        tool_name="FileRead",
        arguments={"path": "public-link.txt"},
    )
    dumped = outcome.model_dump_json(by_alias=True)

    assert outcome.status == "blocked"
    assert outcome.result.error_code == "path_symlink_denied"
    assert "PRIVATE SCRATCH SECRET" not in dumped
    assert "SCRATCHPAD" not in dumped


def test_pr5_file_read_rejects_ancestor_symlink_to_protected_path(
    tmp_path: Path,
) -> None:
    protected_dir = tmp_path / "memory"
    protected_dir.mkdir()
    (protected_dir / "ROOT.md").write_text("PRIVATE MEMORY SECRET\n", encoding="utf-8")
    link_dir = tmp_path / "public"
    try:
        link_dir.symlink_to(protected_dir, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable on this filesystem")

    outcome = _execute(
        workspace_root=tmp_path,
        tool_name="FileRead",
        arguments={"path": "public/ROOT.md"},
    )
    dumped = outcome.model_dump_json(by_alias=True)

    assert outcome.status == "blocked"
    assert outcome.result.error_code == "path_symlink_denied"
    assert "PRIVATE MEMORY SECRET" not in dumped
    assert "memory/ROOT" not in dumped


def test_pr5_glob_skips_private_paths_caps_matches_and_uses_safe_path_refs(
    tmp_path: Path,
) -> None:
    for name in ("visible-a.txt", "visible-b.txt", "visible-c.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    (tmp_path / ".hidden.txt").write_text("hidden", encoding="utf-8")
    (tmp_path / "service-token.txt").write_text("secret", encoding="utf-8")
    (tmp_path / "app-config.json").write_text("private", encoding="utf-8")
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "ROOT.md").write_text("private memory", encoding="utf-8")
    (tmp_path / "SCRATCHPAD.md").write_text("private scratch", encoding="utf-8")

    outcome = _execute(
        workspace_root=tmp_path,
        tool_name="Glob",
        arguments={"pattern": "**/*", "maxMatches": 2},
    )
    dumped = outcome.model_dump_json(by_alias=True)

    assert outcome.status == "ok"
    assert outcome.result.output["truncated"] is True
    assert outcome.result.output["matches"] == (
        {
            "path": "visible-a.txt",
            "pathRef": outcome.result.output["matches"][0]["pathRef"],
            "sourceRef": "src_1",
        },
        {
            "path": "visible-b.txt",
            "pathRef": outcome.result.output["matches"][1]["pathRef"],
            "sourceRef": "src_2",
        },
    )
    assert all(match["sourceRef"].startswith("src_") for match in outcome.result.output["matches"])
    assert ".hidden" not in dumped
    assert "service-token" not in dumped
    assert "app-config" not in dumped
    assert "memory/ROOT" not in dumped
    assert "SCRATCHPAD" not in dumped
    assert "secret" not in dumped


def test_pr5_grep_bounds_files_matches_redacts_snippets_and_projects_sources(
    tmp_path: Path,
) -> None:
    (tmp_path / "one.txt").write_text(
        "needle safe line\nneedle sk-live-secret-token\n",
        encoding="utf-8",
    )
    (tmp_path / "two.txt").write_text("needle second file\n", encoding="utf-8")
    (tmp_path / ".env").write_text("needle TOKEN=live-token\n", encoding="utf-8")
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "ROOT.md").write_text("needle private memory\n", encoding="utf-8")
    (tmp_path / "WORKING.md").write_text("needle private working state\n", encoding="utf-8")

    outcome = _execute(
        workspace_root=tmp_path,
        tool_name="Grep",
        arguments={"pattern": "needle", "glob": "**/*.txt", "maxFiles": 2, "maxMatches": 2},
    )
    dumped = outcome.model_dump_json(by_alias=True)

    assert outcome.status == "ok"
    assert outcome.result.output["truncated"] is True
    assert outcome.result.output["fileCount"] == 2
    assert [match["line"] for match in outcome.result.output["matches"]] == [1, 2]
    assert outcome.result.output["matches"][1]["snippet"] == "needle [redacted]"
    assert outcome.result.metadata["sourceProjection"]["sources"][0]["sourceId"] == "src_1"
    assert ".env" not in dumped
    assert "private memory" not in dumped
    assert "private working" not in dumped
    assert "WORKING" not in dumped
    assert "live-secret-token" not in dumped
    assert "TOKEN=live-token" not in dumped


def test_pr5_grep_redacts_session_key_variants_from_output_and_evidence(
    tmp_path: Path,
) -> None:
    (tmp_path / "safe.txt").write_text(
        "\n".join(
            (
                "needle sessionKey=unsafe-session-key",
                "needle session_id=unsafe-session-id",
                "needle SESSION=unsafe-session",
            )
        ),
        encoding="utf-8",
    )

    outcome = _execute(
        workspace_root=tmp_path,
        tool_name="Grep",
        arguments={"pattern": "needle", "glob": "safe.txt", "maxMatches": 3},
    )
    dumped = outcome.model_dump_json(by_alias=True)
    result_summary = json.dumps(
        outcome.evidence_records[1].result_summary,
        sort_keys=True,
    )

    assert outcome.status == "ok"
    assert all("[redacted]" in match["snippet"] for match in outcome.result.output["matches"])
    assert "unsafe-session-key" not in dumped
    assert "unsafe-session-id" not in dumped
    assert "unsafe-session" not in dumped
    assert "unsafe-session-key" not in result_summary
    assert "unsafe-session-id" not in result_summary
    assert "unsafe-session" not in result_summary
    assert outcome.evidence_records[1].result_summary["llmOutputPreview"] == "[redacted-output]"


def test_pr5_shared_tool_preview_redacts_session_key_values_not_public_refs() -> None:
    from openmagi_core_agent.transport.tool_preview import sanitize_tool_preview

    sanitized = sanitize_tool_preview(
        "sessionKey=unsafe-session-key session_id=unsafe-session-id SESSION=unsafe-session "
        "session:public-ref turn:public-ref"
    )

    assert "unsafe-session-key" not in sanitized
    assert "unsafe-session-id" not in sanitized
    assert "unsafe-session" not in sanitized
    assert "session:public-ref" in sanitized
    assert "turn:public-ref" in sanitized


def test_pr5_redacts_all_authorization_header_schemes_and_public_context_refs(
    tmp_path: Path,
) -> None:
    raw_session = "session-raw-private-key"
    raw_turn = "turn-raw-private-key"
    context = ToolContext(
        botId="bot-pr5",
        userId="user-pr5",
        sessionId=raw_session,
        sessionKey="context-ref-pr5",
        turnId=raw_turn,
        workspaceRoot=str(tmp_path),
    )
    (tmp_path / "headers.txt").write_text(
        "\n".join(
            (
                "Authorization: Basic dXNlcjpwYXNz",
                "Authorization: ApiKey open-sesame",
                "Authorization: Token live-token",
                "needle Authorization: Basic dXNlcjpwYXNz",
            )
        ),
        encoding="utf-8",
    )

    from openmagi_core_agent.tools.local_readonly import LocalReadOnlyToolHost

    registry = _registry("FileRead", "Grep")
    host = LocalReadOnlyToolHost()
    kernel = ToolExecutionKernel(
        registry,
        config=ToolExecutionKernelConfig(enabled=True, localFakeHandlerExecutionEnabled=True),
        local_fake_executor=host,
    )
    file_read = asyncio.run(
        kernel.execute(
            ToolExecutionRequest(
                toolName="FileRead",
                arguments={"path": "headers.txt"},
                context=context,
                mode="act",
                exposedToolNames=("FileRead", "Grep"),
                toolCallId="call-file",
            )
        )
    )
    grep = asyncio.run(
        kernel.execute(
            ToolExecutionRequest(
                toolName="Grep",
                arguments={"pattern": "Authorization", "glob": "headers.txt"},
                context=context,
                mode="act",
                exposedToolNames=("FileRead", "Grep"),
                toolCallId="call-grep",
            )
        )
    )

    dumped = file_read.model_dump_json(by_alias=True) + grep.model_dump_json(by_alias=True)
    assert "Authorization:" not in dumped
    assert "Basic dXNlcjpwYXNz" not in dumped
    assert "ApiKey open-sesame" not in dumped
    assert "Token live-token" not in dumped
    assert raw_session not in dumped
    assert raw_turn not in dumped
    assert "session:" in dumped
    assert "turn:" in dumped


def test_pr5_source_ledger_role_is_harness_context_driven(
    tmp_path: Path,
) -> None:
    (tmp_path / "notes.txt").write_text("research note\n", encoding="utf-8")
    context = ToolContext(
        botId="bot-pr5",
        userId="user-pr5",
        sessionId="session-pr5",
        sessionKey="context-ref-pr5",
        turnId="turn-pr5",
        workspaceRoot=str(tmp_path),
        executionContract={"agentRole": "research"},
    )
    from openmagi_core_agent.tools.local_readonly import LocalReadOnlyToolHost

    registry = _registry("FileRead")
    host = LocalReadOnlyToolHost(agent_role="coding")
    outcome = asyncio.run(
        ToolExecutionKernel(
            registry,
            config=ToolExecutionKernelConfig(enabled=True, localFakeHandlerExecutionEnabled=True),
            local_fake_executor=host,
        ).execute(
            ToolExecutionRequest(
                toolName="FileRead",
                arguments={"path": "notes.txt"},
                context=context,
                mode="act",
                exposedToolNames=("FileRead",),
            )
        )
    )

    assert outcome.status == "ok"
    assert (
        outcome.result.metadata["sourceProjection"]["sources"][0]["scope"]["agentRole"]
        == "research"
    )


def test_pr5_file_read_and_grep_read_only_bounded_bytes_from_large_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openmagi_core_agent.tools import local_readonly

    read_sizes: list[int] = []
    original_read = local_readonly._read_bounded_bytes

    def track_read(path: Path, max_bytes: int) -> bytes:
        read_sizes.append(max_bytes)
        return original_read(path, max_bytes)

    monkeypatch.setattr(local_readonly, "_read_bounded_bytes", track_read)
    (tmp_path / "large.txt").write_text("needle\n" + ("x" * 4096), encoding="utf-8")

    file_read = _execute(
        workspace_root=tmp_path,
        tool_name="FileRead",
        arguments={"path": "large.txt", "maxBytes": 12},
    )
    grep = _execute(
        workspace_root=tmp_path,
        tool_name="Grep",
        arguments={"pattern": "needle", "glob": "large.txt", "maxBytes": 10},
    )

    assert file_read.status == "ok"
    assert file_read.result.output["truncated"] is True
    assert grep.status == "ok"
    assert grep.result.output["truncated"] is True
    assert read_sizes == [12, 10]


def test_pr5_gitdiff_is_fixture_local_subprocess_free_capped_and_source_projected(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    diff = "\n".join(
        [
            "diff --git a/src/app.py b/src/app.py",
            "--- a/src/app.py",
            "+++ b/src/app.py",
            "@@ -1 +1 @@",
            "-print('hello')",
            "+print('needle sk-live-secret-token')",
        ]
    )

    from openmagi_core_agent.tools.local_readonly import LocalReadOnlyToolHost

    diff_ref = f"diff-fixture:{hashlib.sha256(diff.encode('utf-8')).hexdigest()}"
    outcome = _execute(
        workspace_root=tmp_path,
        tool_name="GitDiff",
        arguments={"fixtureDiffRef": diff_ref, "maxBytes": 80},
        host=LocalReadOnlyToolHost(diff_fixtures={diff_ref: diff}),
    )
    dumped = outcome.model_dump_json(by_alias=True)

    assert outcome.status == "ok"
    assert outcome.result.output["subprocessFree"] is True
    assert outcome.result.output["truncated"] is True
    assert outcome.result.output["files"] == (
        {
            "path": "src/app.py",
            "pathRef": outcome.result.output["files"][0]["pathRef"],
            "sourceRef": "src_1",
        },
    )
    assert outcome.result.metadata["sourceEvidenceReceipts"][0]["sourceRef"] == "src_1"
    assert "live-secret-token" not in dumped
    assert str(tmp_path) not in dumped
    result_summary = json.dumps(
        outcome.evidence_records[1].result_summary,
        sort_keys=True,
    )
    assert "diff --git" not in result_summary
    assert "+print" not in result_summary
    assert outcome.evidence_records[1].result_summary["llmOutputPreview"] == "[redacted-output]"


def test_pr5_gitdiff_raw_fixture_body_not_recorded_in_tool_call_evidence(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    diff = "\n".join(
        [
            "diff --git a/src/app.py b/src/app.py",
            "--- a/src/app.py",
            "+++ b/src/app.py",
            "@@ -1 +1 @@",
            "-print('hello')",
            "+print('needle')",
        ]
    )

    from openmagi_core_agent.tools.local_readonly import LocalReadOnlyToolHost

    diff_ref = f"diff-fixture:{hashlib.sha256(diff.encode('utf-8')).hexdigest()}"
    outcome = _execute(
        workspace_root=tmp_path,
        tool_name="GitDiff",
        arguments={"fixtureDiffRef": diff_ref},
        host=LocalReadOnlyToolHost(diff_fixtures={diff_ref: diff}),
    )
    dumped = outcome.model_dump_json(by_alias=True)
    arg_summary_dumped = json.dumps(
        outcome.evidence_records[0].arg_summary,
        sort_keys=True,
    )

    assert outcome.status == "ok"
    assert outcome.evidence_records[0].arg_summary["fixtureDiffRefPreview"] == "[redacted-body]"
    assert "diff --git" not in arg_summary_dumped
    assert "+print('needle')" not in arg_summary_dumped
    assert "diff --git" in dumped


def test_pr5_gitdiff_raw_diff_text_argument_is_redacted_before_blocked_handler_result(
    tmp_path: Path,
) -> None:
    diff = "diff --git a/src/app.py b/src/app.py\n+SECRET"

    outcome = _execute(
        workspace_root=tmp_path,
        tool_name="GitDiff",
        arguments={"diffText": diff},
    )
    dumped_args = json.dumps(outcome.evidence_records[0].arg_summary, sort_keys=True)

    assert outcome.status == "blocked"
    assert outcome.result.error_code == "git_diff_fixture_required"
    assert outcome.evidence_records[0].arg_summary["diffTextPreview"] == "[redacted-body]"
    assert "diff --git" not in dumped_args
    assert "+SECRET" not in dumped_args


def test_pr5_gitdiff_diffref_alias_is_redacted_in_tool_call_evidence(
    tmp_path: Path,
) -> None:
    from openmagi_core_agent.tools.local_readonly import LocalReadOnlyToolHost

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    diff = "diff --git a/src/app.py b/src/app.py\n+SECRET"
    diff_ref = f"diff-fixture:{hashlib.sha256(diff.encode('utf-8')).hexdigest()}"

    outcome = _execute(
        workspace_root=tmp_path,
        tool_name="GitDiff",
        arguments={"diffRef": diff_ref},
        host=LocalReadOnlyToolHost(diff_fixtures={diff_ref: diff}),
    )
    dumped_args = json.dumps(outcome.evidence_records[0].arg_summary, sort_keys=True)

    assert outcome.status == "ok"
    assert outcome.evidence_records[0].arg_summary["diffRefPreview"] == "[redacted-body]"
    assert diff_ref not in dumped_args
    assert "diff --git" not in dumped_args
    assert "+SECRET" not in dumped_args


def test_pr5_local_readonly_import_boundary_avoids_network_live_runtime_and_subprocess() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

before = set(sys.modules)
importlib.import_module("openmagi_core_agent.tools.local_readonly")

forbidden_exact = (
    "google.adk.runners",
    "google.adk.agents",
    "google.adk.sessions",
    "google.adk.models",
    "google.adk.tools",
    "fastapi",
    "uvicorn",
    "supabase",
    "psycopg",
    "asyncpg",
    "kubernetes",
    "httpx",
    "requests",
    "socket",
    "subprocess",
    "openmagi_core_agent.runtime.openmagi_runtime",
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.memory.adk_bridge",
)
loaded = [
    name
    for name in set(sys.modules) - before
    if name in forbidden_exact
    or any(name.startswith(f"{prefix}.") for prefix in forbidden_exact)
]
if loaded:
    raise AssertionError(f"PR5 local_readonly loaded forbidden modules: {loaded}")
""",
        ],
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
