"""Tests for magi_agent.artifacts.file_delivery_live — real filesystem providers.

Covers:
  * LiveFilesystemArtifactProvider.write_artifact — happy path, digest mismatch,
    bad output dir (never raises).
  * LiveFilesystemChannelProvider.deliver — writes file to outbox, returns a
    receipt accepted by the boundary (end-to-end delivered_live assertion).
  * Safe-path: filename containing path traversal is sanitised.
  * is_live_file_delivery_enabled: off by default, on with enabled flag, off with
    kill-switch.
  * Import-boundary: file_delivery_live does NOT import network libs at module level;
    importing file_delivery still does NOT pull in file_delivery_live.
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


def _make_request(
    *,
    content_bytes: bytes,
    filename: str = "report.md",
    channel_type: str = "web",
    channel_id: str = "web-session-1",
    request_id: str = "test-request-1",
) -> object:
    """Build a minimal FileDeliveryRequest suitable for live-provider tests."""
    from magi_agent.artifacts.file_delivery import FileDeliveryRequest
    from magi_agent.channels.contract import ChannelRef

    digest = _sha256(content_bytes)
    artifact_ref = f"artifact:{hashlib.sha1(digest.encode()).hexdigest()[:16]}"
    return FileDeliveryRequest(
        operation="file.deliver",
        requestId=request_id,
        sessionKey="session:livetest",
        channel=ChannelRef(type=channel_type, channelId=channel_id),
        artifactRefs=(artifact_ref,),
        fileRefs=(f"file:{hashlib.sha1(digest.encode()).hexdigest()[:16]}",),
        filename=filename,
        mimeType="text/markdown",
        contentDigest=digest,
    )


# ---------------------------------------------------------------------------
# LiveFilesystemArtifactProvider tests
# ---------------------------------------------------------------------------


def test_artifact_provider_writes_file_and_returns_ok(tmp_path: Path) -> None:
    from magi_agent.artifacts.file_delivery_live import LiveFilesystemArtifactProvider

    content = b"# Hello Live\nThis is a test artifact."
    digest = _sha256(content)
    provider = LiveFilesystemArtifactProvider(
        content_bytes=content,
        output_dir=tmp_path / "output",
        filename="report.md",
    )
    request = _make_request(content_bytes=content)
    result = provider.write_artifact(request)

    assert result["status"] == "ok"
    assert result["contentDigest"] == digest
    assert str(result.get("artifactRef", "")).startswith("artifact:")
    assert str(result.get("receiptId", "")).startswith("fsart:")

    written = tmp_path / "output" / "report.md"
    assert written.exists()
    assert written.read_bytes() == content


def test_artifact_provider_creates_output_dir_if_missing(tmp_path: Path) -> None:
    from magi_agent.artifacts.file_delivery_live import LiveFilesystemArtifactProvider

    content = b"nested directory creation"
    output_dir = tmp_path / "deeply" / "nested" / "dir"
    assert not output_dir.exists()

    provider = LiveFilesystemArtifactProvider(
        content_bytes=content,
        output_dir=output_dir,
        filename="nested.md",
    )
    result = provider.write_artifact(_make_request(content_bytes=content, filename="nested.md"))

    assert result["status"] == "ok"
    assert (output_dir / "nested.md").exists()


def test_artifact_provider_digest_mismatch_returns_error_no_file(tmp_path: Path) -> None:
    from magi_agent.artifacts.file_delivery_live import LiveFilesystemArtifactProvider

    content = b"real content"
    wrong_digest = _sha256(b"different content")
    output_dir = tmp_path / "output"

    from magi_agent.artifacts.file_delivery import FileDeliveryRequest
    from magi_agent.channels.contract import ChannelRef

    request = FileDeliveryRequest(
        operation="file.deliver",
        requestId="mismatch-req",
        sessionKey="session:livetest",
        channel=ChannelRef(type="web", channelId="web-1"),
        artifactRefs=("artifact:test1234567890ab",),
        fileRefs=(),
        filename="report.md",
        mimeType="text/markdown",
        contentDigest=wrong_digest,  # Does NOT match content
    )

    provider = LiveFilesystemArtifactProvider(
        content_bytes=content,
        output_dir=output_dir,
        filename="report.md",
    )
    result = provider.write_artifact(request)

    assert result["status"] == "error"
    assert result.get("reason") == "content_digest_mismatch"
    # No file should have been written.
    assert not output_dir.exists() or not (output_dir / "report.md").exists()


def test_artifact_provider_never_raises_on_bad_dir(tmp_path: Path) -> None:
    from magi_agent.artifacts.file_delivery_live import LiveFilesystemArtifactProvider

    content = b"data"
    # Pass a path where the *parent* is a file, not a dir (triggers OSError on mkdir).
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"")
    bad_dir = blocker / "subdir"  # parent is a file

    provider = LiveFilesystemArtifactProvider(
        content_bytes=content,
        output_dir=bad_dir,
        filename="file.md",
    )
    # Must not raise.
    result = provider.write_artifact(_make_request(content_bytes=content))
    assert isinstance(result, dict)
    assert result.get("status") in ("ok", "error")


def test_artifact_provider_has_correct_digest_in_return(tmp_path: Path) -> None:
    from magi_agent.artifacts.file_delivery_live import LiveFilesystemArtifactProvider

    content = b"verify digest round-trip"
    provider = LiveFilesystemArtifactProvider(
        content_bytes=content,
        output_dir=tmp_path,
        filename="check.md",
    )
    result = provider.write_artifact(_make_request(content_bytes=content, filename="check.md"))

    assert result["status"] == "ok"
    assert result["contentDigest"] == _sha256(content)


# ---------------------------------------------------------------------------
# LiveFilesystemChannelProvider tests
# ---------------------------------------------------------------------------


def test_channel_provider_writes_outbox_file(tmp_path: Path) -> None:
    from magi_agent.artifacts.file_delivery_live import LiveFilesystemChannelProvider

    content = b"outbox payload"
    provider = LiveFilesystemChannelProvider(
        content_bytes=content,
        outbox_dir=tmp_path / "outbox",
    )
    request = _make_request(content_bytes=content, filename="deliver.md")
    receipt = provider.deliver(request)

    assert receipt.status == "sent"
    assert receipt.provider_message_id
    assert receipt.provider_message_id.startswith("fsout:")

    written = tmp_path / "outbox" / "deliver.md"
    assert written.exists()
    assert written.read_bytes() == content


def test_channel_provider_receipt_correlates_request(tmp_path: Path) -> None:
    from magi_agent.artifacts.file_delivery_live import LiveFilesystemChannelProvider

    content = b"correlation check"
    provider = LiveFilesystemChannelProvider(
        content_bytes=content,
        outbox_dir=tmp_path,
    )
    request = _make_request(
        content_bytes=content,
        request_id="corr-req-001",
        channel_id="web-corr-1",
    )
    receipt = provider.deliver(request)

    assert receipt.request_id == "corr-req-001"
    assert receipt.channel.channel_id == "web-corr-1"
    assert receipt.channel.type == "web"
    assert receipt.status == "sent"


def test_channel_provider_never_raises_on_bad_outbox(tmp_path: Path) -> None:
    from magi_agent.artifacts.file_delivery_live import LiveFilesystemChannelProvider

    content = b"data"
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"")
    bad_dir = blocker / "subdir"

    provider = LiveFilesystemChannelProvider(
        content_bytes=content,
        outbox_dir=bad_dir,
    )
    # Must not raise.
    receipt = provider.deliver(_make_request(content_bytes=content))
    assert receipt is not None


# ---------------------------------------------------------------------------
# End-to-end: both live providers through FileDeliveryBoundary → delivered_live
# ---------------------------------------------------------------------------


def test_end_to_end_delivered_live_through_boundary(tmp_path: Path) -> None:
    """Full boundary execution with both real filesystem providers → delivered_live.

    Proves:
    - LiveFilesystemArtifactProvider satisfies the live artifact gate.
    - LiveFilesystemChannelProvider satisfies the live channel gate AND the
      _receipt_mismatch correlation contract.
    - Result status == "delivered_live".
    - Both files are written to disk.
    """
    from magi_agent.artifacts.file_delivery import (
        FileDeliveryBoundary,
        FileDeliveryConfig,
        FileDeliveryRequest,
    )
    from magi_agent.artifacts.file_delivery_live import (
        LiveFilesystemArtifactProvider,
        LiveFilesystemChannelProvider,
    )
    from magi_agent.channels.contract import ChannelRef

    content = b"# Live delivery test\nHello from the live provider."
    digest = _sha256(content)
    artifact_ref = f"artifact:{hashlib.sha1(digest.encode()).hexdigest()[:16]}"
    output_dir = tmp_path / "artifacts"
    outbox_dir = tmp_path / "outbox"

    request = FileDeliveryRequest(
        operation="file.deliver",
        requestId="live-e2e-001",
        sessionKey="session:e2elive",
        channel=ChannelRef(type="web", channelId="web-live-1"),
        artifactRefs=(artifact_ref,),
        fileRefs=(f"file:{hashlib.sha1(digest.encode()).hexdigest()[:16]}",),
        filename="live-report.md",
        mimeType="text/markdown",
        contentDigest=digest,
    )

    artifact_provider = LiveFilesystemArtifactProvider(
        content_bytes=content,
        output_dir=output_dir,
        filename="live-report.md",
    )
    channel_provider = LiveFilesystemChannelProvider(
        content_bytes=content,
        outbox_dir=outbox_dir,
    )

    config = FileDeliveryConfig(
        enabled=True,
        liveArtifactStorageEnabled=True,
        liveChannelDeliveryEnabled=True,
    )
    decision = FileDeliveryBoundary(config).execute(
        request,
        artifact_provider=artifact_provider,
        channel_provider=channel_provider,
    )

    assert decision.status == "delivered_live", (
        f"Expected delivered_live, got {decision.status!r}; "
        f"reason_codes={decision.reason_codes}"
    )
    assert decision.delivery_claim_allowed is True
    assert decision.boundary_verified is True
    assert decision.delivery_receipt is not None
    assert decision.delivery_receipt.status == "sent"
    assert decision.delivery_receipt.provider_message_id

    # Both files must exist on disk.
    assert (output_dir / "live-report.md").exists()
    assert (output_dir / "live-report.md").read_bytes() == content
    assert (outbox_dir / "live-report.md").exists()
    assert (outbox_dir / "live-report.md").read_bytes() == content


# ---------------------------------------------------------------------------
# Safe-path: path traversal in filename is sanitised
# ---------------------------------------------------------------------------


def test_safe_path_traversal_sanitised(tmp_path: Path) -> None:
    from magi_agent.artifacts.file_delivery_live import (
        LiveFilesystemArtifactProvider,
        LiveFilesystemChannelProvider,
    )

    content = b"traversal attempt"
    traversal_filename = "../../etc/passwd"
    output_dir = tmp_path / "output"
    outbox_dir = tmp_path / "outbox"

    # Artifact provider
    artifact_provider = LiveFilesystemArtifactProvider(
        content_bytes=content,
        output_dir=output_dir,
        filename=traversal_filename,
    )
    request = _make_request(content_bytes=content, filename="safe.md")
    artifact_result = artifact_provider.write_artifact(request)

    # Either the write succeeds (sanitised name) or returns error — but NOTHING
    # escapes output_dir.
    if artifact_result.get("status") == "ok":
        # Confirm no file was written outside output_dir.
        escaped = tmp_path / "etc" / "passwd"
        assert not escaped.exists(), "Path traversal escaped output_dir for artifact provider"
        # Confirm the file is inside output_dir.
        written_files = list(output_dir.rglob("*"))
        for f in written_files:
            assert output_dir in f.parents or f == output_dir, (
                f"File escaped output_dir: {f}"
            )

    # Channel provider
    channel_provider = LiveFilesystemChannelProvider(
        content_bytes=content,
        outbox_dir=outbox_dir,
    )
    # Build a request with traversal filename that passes FileDeliveryRequest
    # validation (the model rejects filenames with ".." via field_validator).
    # We test _safe_basename directly via a crafted call to deliver().
    # Since FileDeliveryRequest blocks "../../etc/passwd" at model level,
    # test the channel provider directly using a mock request object.
    class _MockRequest:
        request_id = "trav-001"
        artifact_refs = ("artifact:aaaaaaaaaaaaaaaa",)
        file_refs = ()
        filename = traversal_filename
        from magi_agent.channels.contract import ChannelRef as _CR
        channel = _CR(type="web", channelId="web-1")

    receipt = channel_provider.deliver(_MockRequest())
    # Either sent or failed — but no file outside outbox_dir.
    escaped_outbox = tmp_path / "etc" / "passwd"
    assert not escaped_outbox.exists(), "Path traversal escaped outbox_dir for channel provider"
    if receipt.status == "sent":
        written_outbox = list(outbox_dir.rglob("*"))
        for f in written_outbox:
            assert outbox_dir in f.parents or f == outbox_dir, (
                f"File escaped outbox_dir: {f}"
            )


def test_safe_basename_strips_traversal() -> None:
    from magi_agent.artifacts.file_delivery_live import _safe_basename

    # Path separators are stripped — no traversal possible.
    result_unix = _safe_basename("../../etc/passwd")
    assert "/" not in result_unix
    assert "\\" not in result_unix
    # pathlib.Path("../../etc/passwd").name == "passwd"; no dots remain.
    assert result_unix == "passwd"

    result_win = _safe_basename("..\\windows\\system32\\evil.exe")
    assert "/" not in result_win
    assert "\\" not in result_win

    assert _safe_basename("safe-name.md") == "safe-name.md"
    assert _safe_basename("") == "magi-artifact"
    assert _safe_basename("   ") == "magi-artifact"


# ---------------------------------------------------------------------------
# is_live_file_delivery_enabled tests
# ---------------------------------------------------------------------------


def test_live_file_delivery_disabled_by_default() -> None:
    from magi_agent.artifacts.file_delivery_live import is_live_file_delivery_enabled

    assert is_live_file_delivery_enabled({}) is False


def test_live_file_delivery_enabled_when_flag_set() -> None:
    from magi_agent.artifacts.file_delivery_live import (
        LIVE_FILE_DELIVERY_ENABLED_ENV,
        is_live_file_delivery_enabled,
    )

    assert is_live_file_delivery_enabled({LIVE_FILE_DELIVERY_ENABLED_ENV: "1"}) is True
    assert is_live_file_delivery_enabled({LIVE_FILE_DELIVERY_ENABLED_ENV: "true"}) is True
    assert is_live_file_delivery_enabled({LIVE_FILE_DELIVERY_ENABLED_ENV: "yes"}) is True
    assert is_live_file_delivery_enabled({LIVE_FILE_DELIVERY_ENABLED_ENV: "on"}) is True


def test_live_file_delivery_disabled_when_kill_switch_set() -> None:
    from magi_agent.artifacts.file_delivery_live import (
        LIVE_FILE_DELIVERY_ENABLED_ENV,
        LIVE_FILE_DELIVERY_KILL_SWITCH_ENV,
        is_live_file_delivery_enabled,
    )

    env = {
        LIVE_FILE_DELIVERY_ENABLED_ENV: "1",
        LIVE_FILE_DELIVERY_KILL_SWITCH_ENV: "1",
    }
    assert is_live_file_delivery_enabled(env) is False


def test_live_file_delivery_disabled_falsy_values() -> None:
    from magi_agent.artifacts.file_delivery_live import (
        LIVE_FILE_DELIVERY_ENABLED_ENV,
        is_live_file_delivery_enabled,
    )

    for falsy in ("0", "false", "no", "off", ""):
        assert is_live_file_delivery_enabled({LIVE_FILE_DELIVERY_ENABLED_ENV: falsy}) is False


def test_live_file_delivery_kill_switch_falsy_does_not_block() -> None:
    from magi_agent.artifacts.file_delivery_live import (
        LIVE_FILE_DELIVERY_ENABLED_ENV,
        LIVE_FILE_DELIVERY_KILL_SWITCH_ENV,
        is_live_file_delivery_enabled,
    )

    for falsy in ("0", "false", "no", "off", ""):
        env = {
            LIVE_FILE_DELIVERY_ENABLED_ENV: "1",
            LIVE_FILE_DELIVERY_KILL_SWITCH_ENV: falsy,
        }
        assert is_live_file_delivery_enabled(env) is True


# ---------------------------------------------------------------------------
# Import-boundary tests
# ---------------------------------------------------------------------------


def test_file_delivery_live_has_no_network_imports() -> None:
    """file_delivery_live must NOT import network libs at module level."""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.artifacts.file_delivery_live")
# Intentionally excludes 'socket' and 'urllib' which are loaded transitively
# by pydantic / magi_agent.channels.contract on import (pre-existing behaviour,
# not introduced by file_delivery_live).  The meaningful constraint is that no
# high-level network client library is imported.
forbidden_prefixes = (
    "requests",
    "httpx",
    "urllib3",
    "aiohttp",
    "http.client",
    "telegram",
    "discord",
    "google.adk",
    "magi_agent.transport",
)
loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"forbidden network modules loaded: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_file_delivery_does_not_import_file_delivery_live() -> None:
    """Importing file_delivery must NOT pull in file_delivery_live (lazy import contract)."""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.artifacts.file_delivery")
live_module = "magi_agent.artifacts.file_delivery_live"
if live_module in sys.modules:
    raise AssertionError(
        f"file_delivery imported file_delivery_live at module level: {live_module}"
    )
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
