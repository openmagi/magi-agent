import asyncio
import json
from pathlib import Path

import pytest

from magi_agent.memory.adapters.hipocampus_readonly import (
    HipocampusReadOnlyAdapter,
    HipocampusReadOnlyConfig,
    UnsafeMemoryPathError,
)
from magi_agent.memory.contracts import RecallRequest, UnsupportedMemoryOperationError
from magi_agent.memory.policy import MemoryPolicy


def write_memory_fixture(root: Path) -> None:
    memory_dir = root / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "ROOT.md").write_text(
        "# Root\n\nContinue the launch plan. Token sk-memory-secret should not leak.\n",
        encoding="utf-8",
    )
    (memory_dir / "qmd_results.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "path": "memory/daily/2026-05-18.md",
                        "content": "Launch plan decision from qmd. Authorization: Bearer unsafe.",
                        "score": 0.91,
                        "context": "daily memory",
                    },
                    {
                        "path": "../escape.md",
                        "content": "must be ignored",
                        "score": 0.99,
                    },
                    {
                        "path": "memory/private.md",
                        "content": "unrelated content",
                        "score": 0.2,
                    },
                ]
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def test_readonly_adapter_reports_capabilities_without_write_or_prompt_projection(
    tmp_path: Path,
) -> None:
    write_memory_fixture(tmp_path)
    adapter = HipocampusReadOnlyAdapter(
        HipocampusReadOnlyConfig(workspace_root=tmp_path, enabled=True)
    )

    capabilities = adapter.capabilities()

    assert capabilities.provider_id == "hipocampus-qmd-readonly"
    assert capabilities.supports_search is True
    assert capabilities.supports_write is False
    assert adapter.prompt_projection_enabled is False


def test_readonly_adapter_recalls_root_and_qmd_fixture_records_with_redaction(
    tmp_path: Path,
) -> None:
    write_memory_fixture(tmp_path)
    adapter = HipocampusReadOnlyAdapter(
        HipocampusReadOnlyConfig(workspace_root=tmp_path, enabled=True)
    )

    result = asyncio.run(
        adapter.recall(
            RecallRequest(
                scope={"tenantId": "tenant-1", "botId": "bot-1"},
                query="launch plan",
                purpose="answer_user",
            ),
            policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        )
    )

    assert result.prompt_projection_allowed is False
    assert result.write_allowed is False
    assert result.provider_id == "hipocampus-qmd-readonly"
    assert [record.custom_metadata["sourceKind"] for record in result.records] == [
        "hipocampus_root",
        "qmd_search",
    ]
    assert all(record.source_ref.startswith("memory:sha256:") for record in result.records)
    public = result.public_projection()
    rendered = json.dumps(public, sort_keys=True)
    assert "sk-memory-secret" not in rendered
    assert "Bearer unsafe" not in rendered
    assert "../escape.md" not in rendered


def test_readonly_adapter_blocks_incognito_recall(tmp_path: Path) -> None:
    write_memory_fixture(tmp_path)
    adapter = HipocampusReadOnlyAdapter(
        HipocampusReadOnlyConfig(workspace_root=tmp_path, enabled=True)
    )

    result = asyncio.run(
        adapter.recall(
            RecallRequest(
                scope={"tenantId": "tenant-1", "botId": "bot-1"},
                query="launch plan",
                purpose="audit",
            ),
            policy=MemoryPolicy(memory_mode="incognito", source_authority="long_term_allowed"),
        )
    )

    assert result.records == ()
    assert result.recall_allowed is False
    assert "incognito_blocks_recall" in result.reason_codes


def test_readonly_adapter_rejects_write_delete_and_redact_operations(
    tmp_path: Path,
) -> None:
    write_memory_fixture(tmp_path)
    adapter = HipocampusReadOnlyAdapter(
        HipocampusReadOnlyConfig(workspace_root=tmp_path, enabled=True)
    )

    with pytest.raises(UnsupportedMemoryOperationError, match="read-only"):
        asyncio.run(adapter.remember({"body": "do not write"}))
    with pytest.raises(UnsupportedMemoryOperationError, match="read-only"):
        asyncio.run(adapter.delete("root-memory"))
    with pytest.raises(UnsupportedMemoryOperationError, match="read-only"):
        asyncio.run(adapter.redact("root-memory"))


@pytest.mark.parametrize(
    "workspace_root",
    (
        Path("/data/bots/bot-1/openclaw-home"),
        Path("/workspace/bot-1"),
        Path("/var/lib/kubelet/pods/pvc-unsafe"),
    ),
)
def test_readonly_adapter_rejects_production_workspace_roots(workspace_root: Path) -> None:
    with pytest.raises(UnsafeMemoryPathError):
        HipocampusReadOnlyAdapter(
            HipocampusReadOnlyConfig(workspace_root=workspace_root, enabled=True)
        )


def test_readonly_adapter_rejects_workspace_root_symlink_to_production_path(
    tmp_path: Path,
) -> None:
    symlink_root = tmp_path / "safe-looking-memory-root"
    symlink_root.symlink_to(Path("/workspace/bot-1"), target_is_directory=True)

    with pytest.raises(UnsafeMemoryPathError):
        HipocampusReadOnlyAdapter(
            HipocampusReadOnlyConfig(workspace_root=symlink_root, enabled=True)
        )


def test_readonly_adapter_ignores_qmd_results_outside_workspace(tmp_path: Path) -> None:
    write_memory_fixture(tmp_path)
    adapter = HipocampusReadOnlyAdapter(
        HipocampusReadOnlyConfig(workspace_root=tmp_path, enabled=True)
    )

    result = asyncio.run(
        adapter.search(
            RecallRequest(
                scope={"tenantId": "tenant-1", "botId": "bot-1"},
                query="ignored",
                purpose="debug",
            ),
            policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        )
    )

    assert [record.source_ref for record in result.records] == []
