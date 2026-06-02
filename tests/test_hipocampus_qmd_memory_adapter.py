from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from magi_agent.memory.adapters.hipocampus_readonly import (
    HipocampusReadOnlyAdapter,
    HipocampusReadOnlyConfig,
)
from magi_agent.memory.contracts import RecallRequest, UnsupportedMemoryOperationError
from magi_agent.memory.policy import MemoryPolicy


FAKE_GITHUB_PAT = "github_" + "pat_" + "not_a_real_token_value"
FAKE_PEM_BEGIN = "-----BEGIN OPENSSH " + "PRIVATE KEY-----"
FAKE_PEM_END = "-----END OPENSSH " + "PRIVATE KEY-----"
FAKE_PEM_BODY = "b3BlbnNzaC1rZXktdjE" + "AAAAABBBBBBBBBBBBBBBBBBBBBBBBBBBB"


def write_compat_fixture(root: Path) -> None:
    memory_dir = root / "memory"
    daily_dir = memory_dir / "daily"
    daily_dir.mkdir(parents=True)
    (memory_dir / "ROOT.md").write_text(
        "\n".join(
            (
                "# Root",
                "Launch plan root signal for compatibility.",
                f"Standalone token {FAKE_GITHUB_PAT} must not leak.",
                FAKE_PEM_BEGIN,
                FAKE_PEM_BODY,
                "Cookie: session=unsafe-cookie",
                "<hidden_reasoning>do not expose hidden reasoning</hidden_reasoning>",
                "/Users/kevin/private/provider/path",
            )
        ),
        encoding="utf-8",
    )
    (daily_dir / "2026-05-24.md").write_text(
        "\n".join(
            (
                "# Daily",
                "Launch plan daily note for compatibility.",
                "raw transcript: full raw transcript must not leak",
            )
        ),
        encoding="utf-8",
    )
    (memory_dir / "qmd_results.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "path": "memory/daily/2026-05-24.md",
                        "content": "\n".join(
                            (
                                "QMD launch plan search ref.",
                                "Provider path .qmd/index/cache/raw-memory.bin must not leak.",
                                "C:\\Users\\kevin\\.ssh\\id_rsa must not leak.",
                                "Authorization: Bearer unsafe-token",
                                "/Users/kevin/raw/provider/path",
                            )
                        ),
                        "score": 0.97,
                        "context": "raw provider path /Users/kevin/.qmd/index",
                    },
                    {
                        "path": "/Users/kevin/private/raw-provider.md",
                        "content": "absolute provider path must be ignored",
                        "score": 0.99,
                    },
                    {
                        "path": "memory/daily/low-score.md",
                        "content": "Launch plan low score should not appear.",
                        "score": 0.01,
                    },
                    {
                        "path": "memory/daily/key-tail.md",
                        "content": "\n".join(
                            (
                                "Launch plan key tail should be quarantined.",
                                FAKE_PEM_BODY,
                                FAKE_PEM_END,
                            )
                        ),
                        "score": 0.96,
                    },
                ]
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _request(*, max_bytes: int = 160) -> RecallRequest:
    return RecallRequest(
        scope={"tenantId": "tenant-1", "botId": "bot-1"},
        query="launch plan",
        purpose="answer_user",
        maxBytes=max_bytes,
    )


def _policy() -> MemoryPolicy:
    return MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed")


def test_hipocampus_qmd_adapter_returns_digest_refs_and_sanitized_budgeted_snippets(
    tmp_path: Path,
) -> None:
    write_compat_fixture(tmp_path)
    adapter = HipocampusReadOnlyAdapter(
        HipocampusReadOnlyConfig(workspace_root=tmp_path, enabled=True)
    )

    result = asyncio.run(adapter.recall(_request(max_bytes=240), policy=_policy()))

    assert result.prompt_projection_allowed is False
    assert result.write_allowed is False
    assert {record.custom_metadata["sourceKind"] for record in result.records} == {
        "hipocampus_root",
        "hipocampus_daily",
        "qmd_search",
    }
    assert sum(len(record.body.encode("utf-8")) for record in result.records) <= 240
    for record in result.records:
        assert record.id.startswith("memory:sha256:")
        assert record.source_ref.startswith("memory:sha256:")
        assert record.body

    rendered = result.model_dump_json(by_alias=True)
    public_rendered = json.dumps(result.public_projection(), sort_keys=True)
    forbidden = (
        "Cookie:",
        "unsafe-cookie",
        "Authorization:",
        "unsafe-token",
        FAKE_GITHUB_PAT,
        "PRIVATE KEY",
        FAKE_PEM_BODY[:20],
        "hidden_reasoning",
        "raw transcript",
        "C:\\Users\\kevin",
        "/Users/kevin",
        "memory/ROOT.md",
        "memory/daily/2026-05-24.md",
        ".qmd/index",
        "provider path",
        "raw provider path",
        ".qmd/index",
    )
    for text in forbidden:
        assert text not in rendered
        assert text not in public_rendered


def test_hipocampus_qmd_adapter_enforces_tiny_output_budget(tmp_path: Path) -> None:
    write_compat_fixture(tmp_path)
    adapter = HipocampusReadOnlyAdapter(
        HipocampusReadOnlyConfig(workspace_root=tmp_path, enabled=True)
    )

    result = asyncio.run(adapter.recall(_request(max_bytes=2), policy=_policy()))

    assert sum(len(record.body.encode("utf-8")) for record in result.records) <= 2


def test_hipocampus_qmd_adapter_daily_glob_is_confined_to_daily_memory(
    tmp_path: Path,
) -> None:
    write_compat_fixture(tmp_path)
    (tmp_path / "workspace-note.md").write_text(
        "Launch plan arbitrary workspace markdown must not be read as daily memory.",
        encoding="utf-8",
    )
    adapter = HipocampusReadOnlyAdapter(
        HipocampusReadOnlyConfig(
            workspaceRoot=tmp_path,
            enabled=True,
            dailyMemoryGlob="*.md",
        )
    )

    result = asyncio.run(adapter.recall(_request(max_bytes=500), policy=_policy()))
    rendered = result.model_dump_json(by_alias=True)

    assert "arbitrary workspace markdown" not in rendered


def test_hipocampus_qmd_adapter_search_returns_qmd_refs_only(tmp_path: Path) -> None:
    write_compat_fixture(tmp_path)
    adapter = HipocampusReadOnlyAdapter(
        HipocampusReadOnlyConfig(workspace_root=tmp_path, enabled=True)
    )

    result = asyncio.run(adapter.search(_request(), policy=_policy()))

    assert {record.custom_metadata["sourceKind"] for record in result.records} == {
        "qmd_search"
    }
    assert all(record.source_ref.startswith("memory:sha256:") for record in result.records)
    assert any("QMD launch plan search ref." in record.body for record in result.records)


def test_hipocampus_qmd_adapter_denies_write_compaction_and_erase(
    tmp_path: Path,
) -> None:
    write_compat_fixture(tmp_path)
    adapter = HipocampusReadOnlyAdapter(
        HipocampusReadOnlyConfig(workspace_root=tmp_path, enabled=True)
    )

    with pytest.raises(UnsupportedMemoryOperationError, match="read-only"):
        asyncio.run(adapter.remember({"body": "do not write"}))
    with pytest.raises(UnsupportedMemoryOperationError, match="read-only"):
        asyncio.run(adapter.compact(["memory:sha256:abc"]))
    with pytest.raises(UnsupportedMemoryOperationError, match="read-only"):
        asyncio.run(adapter.erase("memory:sha256:abc"))


def test_hipocampus_qmd_adapter_import_boundary_is_provider_and_network_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.memory.adapters.hipocampus_readonly")
importlib.import_module("magi_agent.memory.adapters")

forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.adk_bridge.local_runner",
    "magi_agent.adk_bridge.memory_service",
    "magi_agent.app",
    "magi_agent.transport.chat",
    "magi_agent.routes",
    "magi_agent.plugins.agentmemory",
    "magi_agent.services.memory",
    "magi_agent.hipocampus",
    "magi_agent.qmd",
    "supabase",
    "psycopg",
    "asyncpg",
    "boto3",
    "requests",
    "httpx",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"memory adapter import loaded forbidden modules: {loaded}")
""",
        ],
        check=False,
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
