"""Tests for build_file_delivery_providers factory and documents.py tool wiring.

Covers:
  * Factory unit tests: gate off → fake config+providers; gate on → live config+providers;
    kill-switch → fake path (default-OFF guarantee).
  * Default-OFF parity: calling file_deliver/file_send with no env gate yields
    delivered_local_fake, output shape identical to before the factory was introduced.
  * Live-on path: MAGI_FILE_DELIVERY_LIVE_ENABLED=1 → factory returns live config +
    live providers; running end-to-end through the tool produces delivered_live and files
    written to the workspace.
  * Import boundary: importing documents must NOT pull in file_delivery_live at
    module level.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _make_tool_context(workspace: Path) -> object:
    """Build a minimal ToolContext pointing at ``workspace``."""
    from magi_agent.tools.context import ToolContext

    return ToolContext(
        workspaceRoot=str(workspace),
        sessionId="test-session",
        botId="test-bot",
        channel="local",
    )


# ---------------------------------------------------------------------------
# Factory unit tests: gate off
# ---------------------------------------------------------------------------


def test_factory_gate_off_returns_fake_config_and_fake_providers(tmp_path: Path) -> None:
    from magi_agent.artifacts.file_delivery import FileDeliveryConfig
    from magi_agent.artifacts.file_delivery_live import build_file_delivery_providers

    content = b"# test content"
    ctx = _make_tool_context(tmp_path)

    config, artifact_provider, channel_provider = build_file_delivery_providers(
        env={},  # No MAGI_FILE_DELIVERY_LIVE_ENABLED
        content_bytes=content,
        filename="test.md",
        context=ctx,
    )

    assert isinstance(config, FileDeliveryConfig)
    assert config.local_fake_artifact_service_enabled is True
    assert config.local_fake_channel_delivery_enabled is True
    assert config.live_artifact_storage_enabled is False
    assert config.live_channel_delivery_enabled is False
    # Providers are fake (local fake marker).
    assert getattr(artifact_provider, "openmagi_local_fake_provider", False) is True
    assert getattr(channel_provider, "openmagi_local_fake_provider", False) is True
    assert getattr(artifact_provider, "openmagi_live_provider", False) is False
    assert getattr(channel_provider, "openmagi_live_provider", False) is False


def test_factory_gate_off_literal_false_production_flags_untouched(tmp_path: Path) -> None:
    """Literal[False] fields must remain False regardless of gate state."""
    from magi_agent.artifacts.file_delivery_live import build_file_delivery_providers

    ctx = _make_tool_context(tmp_path)
    config, _, _ = build_file_delivery_providers(
        env={},
        content_bytes=b"data",
        filename="data.md",
        context=ctx,
    )

    assert config.production_storage_writes_enabled is False
    assert config.production_channel_delivery_enabled is False
    assert config.route_attached is False


# ---------------------------------------------------------------------------
# Factory unit tests: gate on
# ---------------------------------------------------------------------------


def test_factory_gate_on_returns_live_config_and_live_providers(tmp_path: Path) -> None:
    from magi_agent.artifacts.file_delivery import FileDeliveryConfig
    from magi_agent.artifacts.file_delivery_live import (
        LIVE_FILE_DELIVERY_ENABLED_ENV,
        LiveFilesystemArtifactProvider,
        LiveFilesystemChannelProvider,
        build_file_delivery_providers,
    )

    content = b"# live content"
    ctx = _make_tool_context(tmp_path)

    config, artifact_provider, channel_provider = build_file_delivery_providers(
        env={LIVE_FILE_DELIVERY_ENABLED_ENV: "1"},
        content_bytes=content,
        filename="live.md",
        context=ctx,
    )

    assert isinstance(config, FileDeliveryConfig)
    assert config.live_artifact_storage_enabled is True
    assert config.live_channel_delivery_enabled is True
    assert config.local_fake_artifact_service_enabled is False
    assert config.local_fake_channel_delivery_enabled is False
    assert isinstance(artifact_provider, LiveFilesystemArtifactProvider)
    assert isinstance(channel_provider, LiveFilesystemChannelProvider)
    # Both live providers carry openmagi_live_provider = True.
    assert getattr(artifact_provider, "openmagi_live_provider", False) is True
    assert getattr(channel_provider, "openmagi_live_provider", False) is True
    # Neither live provider carries the fake marker.
    assert getattr(artifact_provider, "openmagi_local_fake_provider", False) is False
    assert getattr(channel_provider, "openmagi_local_fake_provider", False) is False


def test_factory_gate_on_live_literal_false_production_flags_untouched(tmp_path: Path) -> None:
    from magi_agent.artifacts.file_delivery_live import (
        LIVE_FILE_DELIVERY_ENABLED_ENV,
        build_file_delivery_providers,
    )

    ctx = _make_tool_context(tmp_path)
    config, _, _ = build_file_delivery_providers(
        env={LIVE_FILE_DELIVERY_ENABLED_ENV: "1"},
        content_bytes=b"data",
        filename="data.md",
        context=ctx,
    )

    assert config.production_storage_writes_enabled is False
    assert config.production_channel_delivery_enabled is False
    assert config.route_attached is False


# ---------------------------------------------------------------------------
# Factory unit tests: kill-switch
# ---------------------------------------------------------------------------


def test_factory_kill_switch_overrides_enabled_returns_fake_path(tmp_path: Path) -> None:
    from magi_agent.artifacts.file_delivery_live import (
        LIVE_FILE_DELIVERY_ENABLED_ENV,
        LIVE_FILE_DELIVERY_KILL_SWITCH_ENV,
        build_file_delivery_providers,
    )

    ctx = _make_tool_context(tmp_path)
    env = {
        LIVE_FILE_DELIVERY_ENABLED_ENV: "1",
        LIVE_FILE_DELIVERY_KILL_SWITCH_ENV: "1",
    }
    config, artifact_provider, channel_provider = build_file_delivery_providers(
        env=env,
        content_bytes=b"data",
        filename="data.md",
        context=ctx,
    )

    # Kill-switch forces fake path.
    assert config.local_fake_artifact_service_enabled is True
    assert config.live_artifact_storage_enabled is False
    assert getattr(artifact_provider, "openmagi_local_fake_provider", False) is True
    assert getattr(channel_provider, "openmagi_local_fake_provider", False) is True


# ---------------------------------------------------------------------------
# Default-OFF parity: file_deliver tool produces delivered_local_fake
# ---------------------------------------------------------------------------


def test_file_deliver_tool_default_off_yields_delivered_local_fake(tmp_path: Path) -> None:
    """With no env gate, the tool returns delivered_local_fake — identical to before
    the factory was introduced.  Checks output shape stability.
    """
    from magi_agent.plugins.native.documents import file_deliver

    # Create a real file in the workspace.
    content = b"# Hello\nTest file delivery."
    test_file = tmp_path / "report.md"
    test_file.write_bytes(content)

    ctx = _make_tool_context(tmp_path)
    result = file_deliver({"path": "report.md"}, ctx)  # type: ignore[arg-type]

    assert result.status == "ok"
    assert isinstance(result.output, dict)
    projection = result.output
    assert projection["status"] == "delivered_local_fake"
    assert projection["deliveryClaimAllowed"] is True
    assert projection["deliveryReceipt"] is not None
    assert projection["deliveryReceipt"]["status"] == "sent"
    assert projection["deliveryReceipt"]["providerMessageId"] is not None
    assert projection["artifactRef"] is not None
    # No paths or secrets in output.
    import json as _json
    rendered = _json.dumps(projection, sort_keys=True)
    assert str(tmp_path) not in rendered
    assert "workspace" not in rendered.lower() or "localOnly" in rendered


def test_file_send_tool_default_off_yields_delivered_local_fake(tmp_path: Path) -> None:
    from magi_agent.plugins.native.documents import file_send

    content = b"Spreadsheet data"
    test_file = tmp_path / "data.csv"
    test_file.write_bytes(content)

    ctx = _make_tool_context(tmp_path)
    result = file_send({"path": "data.csv"}, ctx)  # type: ignore[arg-type]

    assert result.status == "ok"
    assert result.output["status"] == "delivered_local_fake"
    assert result.output["deliveryClaimAllowed"] is True


def test_file_deliver_default_off_missing_file_is_blocked(tmp_path: Path) -> None:
    from magi_agent.plugins.native.documents import file_deliver

    ctx = _make_tool_context(tmp_path)
    result = file_deliver({"path": "does-not-exist.md"}, ctx)  # type: ignore[arg-type]

    assert result.status == "blocked"
    assert result.error_code == "file_not_found"


# ---------------------------------------------------------------------------
# Live-on path: MAGI_FILE_DELIVERY_LIVE_ENABLED=1 → delivered_live + files on disk
# ---------------------------------------------------------------------------


def test_file_deliver_tool_live_on_yields_delivered_live_and_files_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the gate on, file_deliver should yield delivered_live and write files."""
    from magi_agent.artifacts.file_delivery_live import LIVE_FILE_DELIVERY_ENABLED_ENV
    from magi_agent.plugins.native.documents import file_deliver

    monkeypatch.setenv(LIVE_FILE_DELIVERY_ENABLED_ENV, "1")

    content = b"# Live delivery content\nHello world."
    test_file = tmp_path / "live-report.md"
    test_file.write_bytes(content)

    ctx = _make_tool_context(tmp_path)
    result = file_deliver({"path": "live-report.md"}, ctx)  # type: ignore[arg-type]

    assert result.status == "ok"
    projection = result.output
    assert projection["status"] == "delivered_live"
    assert projection["deliveryClaimAllowed"] is True
    assert projection["deliveryReceipt"] is not None
    assert projection["deliveryReceipt"]["status"] == "sent"

    # Files should be written under the workspace.
    artifact_dir = tmp_path / ".magi" / "deliveries" / "artifacts"
    outbox_dir = tmp_path / ".magi" / "deliveries" / "outbox"
    assert artifact_dir.exists(), f"Artifact directory not created: {artifact_dir}"
    assert outbox_dir.exists(), f"Outbox directory not created: {outbox_dir}"

    artifact_files = list(artifact_dir.rglob("*"))
    outbox_files = list(outbox_dir.rglob("*"))
    assert any(f.is_file() for f in artifact_files), "No file written to artifact dir"
    assert any(f.is_file() for f in outbox_files), "No file written to outbox dir"

    # Content written must match original.
    artifact_file = next(f for f in artifact_files if f.is_file())
    outbox_file = next(f for f in outbox_files if f.is_file())
    assert artifact_file.read_bytes() == content
    assert outbox_file.read_bytes() == content


def test_file_deliver_tool_live_kill_switch_falls_back_to_fake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from magi_agent.artifacts.file_delivery_live import (
        LIVE_FILE_DELIVERY_ENABLED_ENV,
        LIVE_FILE_DELIVERY_KILL_SWITCH_ENV,
    )
    from magi_agent.plugins.native.documents import file_deliver

    monkeypatch.setenv(LIVE_FILE_DELIVERY_ENABLED_ENV, "1")
    monkeypatch.setenv(LIVE_FILE_DELIVERY_KILL_SWITCH_ENV, "1")

    content = b"kill-switch test content"
    test_file = tmp_path / "doc.md"
    test_file.write_bytes(content)

    ctx = _make_tool_context(tmp_path)
    result = file_deliver({"path": "doc.md"}, ctx)  # type: ignore[arg-type]

    assert result.status == "ok"
    # Kill-switch forces fake path.
    assert result.output["status"] == "delivered_local_fake"

    # No live delivery dirs should have been created.
    artifact_dir = tmp_path / ".magi" / "deliveries" / "artifacts"
    assert not artifact_dir.exists(), "Unexpected artifact dir created despite kill-switch"


# ---------------------------------------------------------------------------
# Factory: env var overrides for workspace dirs
# ---------------------------------------------------------------------------


def test_factory_gate_on_custom_artifact_dir_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MAGI_FILE_DELIVERY_ARTIFACT_DIR overrides the default artifact subdir."""
    from magi_agent.artifacts.file_delivery_live import (
        LIVE_FILE_DELIVERY_ENABLED_ENV,
        MAGI_FILE_DELIVERY_ARTIFACT_DIR_ENV,
        LiveFilesystemArtifactProvider,
        build_file_delivery_providers,
    )

    ctx = _make_tool_context(tmp_path)
    config, artifact_provider, _ = build_file_delivery_providers(
        env={
            LIVE_FILE_DELIVERY_ENABLED_ENV: "1",
            MAGI_FILE_DELIVERY_ARTIFACT_DIR_ENV: ".magi/deliveries/custom-artifacts",
        },
        content_bytes=b"custom dir test",
        filename="file.md",
        context=ctx,
    )

    assert config.live_artifact_storage_enabled is True
    assert isinstance(artifact_provider, LiveFilesystemArtifactProvider)


# ---------------------------------------------------------------------------
# Import boundary: documents must NOT import file_delivery_live at module level
# ---------------------------------------------------------------------------


def test_documents_does_not_import_file_delivery_live_at_module_level() -> None:
    """Importing magi_agent.plugins.native.documents must NOT pull in
    file_delivery_live (the lazy-import contract).
    """
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.plugins.native.documents")
live_module = "magi_agent.artifacts.file_delivery_live"
if live_module in sys.modules:
    raise AssertionError(
        f"documents imported file_delivery_live at module level: {live_module}"
    )
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
