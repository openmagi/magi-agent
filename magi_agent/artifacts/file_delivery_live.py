"""Real filesystem-backed providers for the FileDelivery boundary.

Default OFF — activated only when ``MAGI_FILE_DELIVERY_LIVE_ENABLED`` is set to
a truthy value AND ``MAGI_FILE_DELIVERY_LIVE_KILL_SWITCH`` is NOT set.

Architecture
------------
Two provider classes implement the live port Protocols defined in
``file_delivery.py``:

* ``LiveFilesystemArtifactProvider`` — writes the artifact file to a configured
  output directory and verifies the sha256 content digest before writing.
* ``LiveFilesystemChannelProvider`` — writes the file to a local "outbox"
  directory (the concrete OSS delivery sink) and constructs a valid
  ``ChannelDeliveryReceipt`` that satisfies the boundary's ``_receipt_mismatch``
  correlation contract.

Both providers carry ``openmagi_live_provider = True`` so
``_is_trusted_live_provider()`` admits them through the live gate.

Env-gate helpers
----------------
``is_live_file_delivery_enabled(env)`` is the call-time gate (never import-time)
so tests can patch ``os.environ`` without a module reload.

Forbidden imports (import-clean by design)
------------------------------------------
No ``requests``/``httpx``/``urllib``/``socket`` at top level.  This module is
pure filesystem logic; no network I/O is performed here.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import re
from collections.abc import Mapping
from typing import Any

from magi_agent.channels.contract import ChannelDeliveryReceipt, ChannelRef


# ---------------------------------------------------------------------------
# Env-gate constants and helper
# ---------------------------------------------------------------------------

LIVE_FILE_DELIVERY_ENABLED_ENV = "MAGI_FILE_DELIVERY_LIVE_ENABLED"
LIVE_FILE_DELIVERY_KILL_SWITCH_ENV = "MAGI_FILE_DELIVERY_LIVE_KILL_SWITCH"

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def is_live_file_delivery_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return True iff live file delivery is enabled and the kill-switch is not set.

    Evaluated at call time (not import time) so tests can patch ``os.environ``
    without a module reload.

    Both the enabled flag and the kill-switch use explicit allowlisting against
    ``_TRUTHY``: a value enables/kills only if it is in that set (case-insensitive
    after strip).  Any other value (including empty string) is treated as false.

    :param env: Optional explicit env mapping; defaults to ``os.environ``.
    """
    source: Mapping[str, str] = env if env is not None else os.environ
    enabled_raw = source.get(LIVE_FILE_DELIVERY_ENABLED_ENV, "")
    kill_raw = source.get(LIVE_FILE_DELIVERY_KILL_SWITCH_ENV, "")
    enabled = enabled_raw.strip().lower() in _TRUTHY
    killed = kill_raw.strip().lower() in _TRUTHY
    return enabled and not killed


# ---------------------------------------------------------------------------
# Safe basename helper
# ---------------------------------------------------------------------------

_UNSAFE_CHARS = re.compile(r"[/\\]")
_LEADING_DOTS = re.compile(r"^\.+")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _safe_basename(filename: str) -> str:
    """Return a safe basename from ``filename``.

    Strips directory separators and leading dots to prevent path traversal.
    Uses pathlib.Path.name first to extract just the final component, then
    replaces remaining unsafe characters.  Truncates to 200 characters.
    Falls back to ``magi-artifact`` if the result would be empty.
    """
    # First extract the basename (handles both / and \ separators).
    stripped = filename.strip()
    # Replace backslashes so pathlib handles them correctly on all platforms.
    normalized = stripped.replace("\\", "/")
    base = pathlib.Path(normalized).name
    # Strip ASCII control characters (null bytes, newlines, etc.) before any
    # further processing so they can never reach write_bytes.
    base = _CONTROL_CHARS.sub("", base)
    # Strip remaining unsafe chars and leading dots.
    base = _UNSAFE_CHARS.sub("_", base)
    base = _LEADING_DOTS.sub("", base)
    base = base.strip(".")
    base = base[:200].strip()
    return base or "magi-artifact"


# ---------------------------------------------------------------------------
# Shared safe-write helper
# ---------------------------------------------------------------------------


def _safe_write_bytes(output_dir: pathlib.Path, filename: str, content: bytes) -> pathlib.Path:
    """Safely write ``content`` to ``output_dir / safe(filename)``.

    Creates ``output_dir`` (and parents) if needed.  Raises ``ValueError`` with
    a ``path_escape_blocked:<dest>`` message if the resolved destination would
    escape ``output_dir``.  Callers are responsible for converting that to an
    appropriate error return; the outer try/except in each provider handles it.
    """
    safe_name = _safe_basename(filename)
    resolved = output_dir.resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    dest = resolved / safe_name
    dest_resolved = dest.resolve()
    if resolved not in dest_resolved.parents and dest_resolved != resolved:
        raise ValueError(f"path_escape_blocked:{dest_resolved}")
    dest.write_bytes(content)
    return dest


# ---------------------------------------------------------------------------
# LiveFilesystemArtifactProvider
# ---------------------------------------------------------------------------


class LiveFilesystemArtifactProvider:
    """Filesystem-backed live artifact provider.

    Writes ``content_bytes`` to ``output_dir/<safe filename>`` after verifying
    that the sha256 digest matches ``request.contentDigest``.

    Implements ``LiveFileArtifactProviderPort``.
    """

    openmagi_live_provider: bool = True

    def __init__(
        self,
        *,
        content_bytes: bytes,
        output_dir: pathlib.Path,
        filename: str,
    ) -> None:
        self._content_bytes = content_bytes
        self._output_dir = output_dir
        self._filename = filename

    def write_artifact(self, request: Any) -> Mapping[str, object]:
        """Write the artifact to disk and return a storage-return mapping.

        Returns ``{"status": "error", "reason": "content_digest_mismatch"}``
        if the computed sha256 of ``content_bytes`` does not equal
        ``request.contentDigest``.  Never raises.
        """
        try:
            return self._write_artifact_inner(request)
        except Exception as exc:  # noqa: BLE001
            _ = exc
            return {"status": "error", "reason": "artifact_write_failed"}

    def _write_artifact_inner(self, request: Any) -> Mapping[str, object]:
        content_bytes = self._content_bytes
        # Compute and verify digest BEFORE touching the filesystem.
        actual_digest = "sha256:" + hashlib.sha256(content_bytes).hexdigest()
        expected_digest: str = request.content_digest
        if actual_digest != expected_digest:
            return {"status": "error", "reason": "content_digest_mismatch"}

        # Delegate safe-path + write to shared helper (raises ValueError on escape).
        _safe_write_bytes(self._output_dir, self._filename, content_bytes)

        # Build the artifactRef: prefer the first element from request.artifact_refs
        # if available, otherwise derive from digest.
        artifact_refs = getattr(request, "artifact_refs", ())
        if artifact_refs:
            artifact_ref = artifact_refs[0]
        else:
            artifact_ref = f"artifact:{hashlib.sha1(actual_digest.encode('utf-8')).hexdigest()[:16]}"

        # Reuse already-computed digest hex (strip "sha256:" prefix, take first 16 chars).
        short_digest = actual_digest[7:23]
        receipt_id = f"fsart:{short_digest}"

        return {
            "status": "ok",
            "artifactRef": artifact_ref,
            "contentDigest": actual_digest,
            "receiptId": receipt_id,
        }


# ---------------------------------------------------------------------------
# LiveFilesystemChannelProvider
# ---------------------------------------------------------------------------


class LiveFilesystemChannelProvider:
    """Filesystem-backed live channel delivery provider (local outbox sink).

    Writes the file to ``outbox_dir/<safe filename>`` and returns a
    ``ChannelDeliveryReceipt`` that satisfies the boundary's
    ``_receipt_mismatch`` correlation:
      - ``receipt.request_id == request.request_id``
      - ``receipt.channel`` matches ``request.channel``
      - ``set(receipt.artifact_refs).intersection(request.artifact_refs)`` is
        non-empty (uses the delivery_request's artifact_refs, already updated
        to ``(artifact_ref,)`` by the boundary before calling deliver)

    Implements ``LiveFileChannelDeliveryPort``.
    """

    openmagi_live_provider: bool = True

    def __init__(
        self,
        *,
        content_bytes: bytes,
        outbox_dir: pathlib.Path,
    ) -> None:
        self._content_bytes = content_bytes
        self._outbox_dir = outbox_dir

    def deliver(self, request: Any) -> ChannelDeliveryReceipt:
        """Deliver the file to the local outbox and return a correlated receipt.

        On failure, returns a receipt with ``status="failed"`` so the boundary
        records a block cleanly (the boundary checks ``receipt.status != "sent"``
        and returns ``"blocked"`` with reason ``"channel_delivery_failed"``).
        Never raises.
        """
        try:
            return self._deliver_inner(request)
        except Exception as exc:  # noqa: BLE001
            _ = exc
            channel = getattr(request, "channel", None)
            if channel is None:
                channel = ChannelRef(type="web", channelId="fallback")
            request_id = getattr(request, "request_id", "unknown")
            short = hashlib.sha256(self._content_bytes).hexdigest()[:16]
            return ChannelDeliveryReceipt(
                receiptId=f"fsout-err:{short}",
                requestId=request_id,
                channel=channel,
                status="failed",
                providerMessageId=None,
                artifactRefs=(),
                fileRefs=(),
            )

    def _deliver_inner(self, request: Any) -> ChannelDeliveryReceipt:
        content_bytes = self._content_bytes
        channel: ChannelRef = request.channel
        request_id: str = request.request_id
        artifact_refs: tuple[str, ...] = getattr(request, "artifact_refs", ())
        file_refs: tuple[str, ...] = getattr(request, "file_refs", ())
        filename: str = getattr(request, "filename", "magi-artifact")

        # Delegate safe-path + write to shared helper (raises ValueError on escape).
        _safe_write_bytes(self._outbox_dir, filename, content_bytes)

        short_digest = hashlib.sha256(content_bytes).hexdigest()[:16]
        provider_message_id = f"fsout:{short_digest}"
        receipt_id = f"fsout-receipt:{short_digest}"

        return ChannelDeliveryReceipt(
            receiptId=receipt_id,
            requestId=request_id,
            channel=channel,
            status="sent",
            providerMessageId=provider_message_id,
            artifactRefs=artifact_refs,
            fileRefs=file_refs,
        )


# ---------------------------------------------------------------------------
# Inline fake providers (used by build_file_delivery_providers gate-off path)
# ---------------------------------------------------------------------------
# These are equivalents of _LocalFakeFileArtifactProvider /
# _LocalFakeChannelDeliveryProvider in documents.py.  They are duplicated here
# (rather than imported from documents.py) to avoid a circular import:
# file_delivery_live ← lazy-import ← documents ← lazy-import ← file_delivery_live.


class _FactoryLocalFakeArtifactProvider:
    openmagi_local_fake_provider = True

    def __init__(self, *, artifact_ref: str, content_digest: str) -> None:
        self._artifact_ref = artifact_ref
        self._content_digest = content_digest

    def write_artifact(self, request: Any) -> Mapping[str, object]:
        request_id: str = getattr(request, "request_id", "unknown")
        return {
            "status": "ok",
            "artifactRef": self._artifact_ref,
            "contentDigest": self._content_digest,
            "receiptId": f"artifact-receipt:{hashlib.sha1(request_id.encode('utf-8')).hexdigest()[:16]}",
        }


class _FactoryLocalFakeChannelProvider:
    openmagi_local_fake_provider = True

    def deliver(self, request: Any) -> "ChannelDeliveryReceipt":
        channel = request.channel
        if channel is None:
            raise ValueError("channel_required")
        request_id: str = request.request_id
        artifact_refs: tuple[str, ...] = getattr(request, "artifact_refs", ())
        file_refs: tuple[str, ...] = getattr(request, "file_refs", ())
        short = hashlib.sha1(request_id.encode("utf-8")).hexdigest()[:16]
        return ChannelDeliveryReceipt(
            receiptId=f"receipt:{short}",
            requestId=request_id,
            channel=channel,
            status="sent",
            providerMessageId=f"message:{hashlib.sha1((request_id + ':message').encode('utf-8')).hexdigest()[:16]}",
            artifactRefs=artifact_refs,
            fileRefs=file_refs,
        )


# ---------------------------------------------------------------------------
# Env var names for workspace delivery directories
# ---------------------------------------------------------------------------

MAGI_FILE_DELIVERY_ARTIFACT_DIR_ENV = "MAGI_FILE_DELIVERY_ARTIFACT_DIR"
MAGI_FILE_DELIVERY_OUTBOX_DIR_ENV = "MAGI_FILE_DELIVERY_OUTBOX_DIR"

_DEFAULT_ARTIFACT_SUBDIR = ".magi/deliveries/artifacts"
_DEFAULT_OUTBOX_SUBDIR = ".magi/deliveries/outbox"


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------


def build_file_delivery_providers(
    *,
    env: Mapping[str, str] | None = None,
    content_bytes: bytes,
    filename: str,
    context: object,
) -> "tuple[object, object, object]":
    """Build (FileDeliveryConfig, artifact_provider, channel_provider) from env.

    When ``MAGI_FILE_DELIVERY_LIVE_ENABLED`` is set (and the kill-switch is NOT
    set), returns live filesystem providers that write files under the workspace.
    Otherwise returns the local-fake providers used today — byte-identical to
    current behaviour (default-OFF guarantee).

    Workspace directories
    ---------------------
    - Artifact output:  ``<workspace>/.magi/deliveries/artifacts``  (or the path
      in ``MAGI_FILE_DELIVERY_ARTIFACT_DIR`` relative to the workspace root).
    - Channel outbox:   ``<workspace>/.magi/deliveries/outbox``  (or
      ``MAGI_FILE_DELIVERY_OUTBOX_DIR``).

    Both paths are validated via ``safe_child_path(allow_internal=True)`` so they
    can never escape the workspace.  On any resolution error the factory falls
    back to the fake path (fail-open, never raises for normal inputs).

    Parameters
    ----------
    env:
        Environment mapping; defaults to ``os.environ``.
    content_bytes:
        Raw file bytes (needed to construct the providers).
    filename:
        Original filename (used for the artifact provider).
    context:
        ``ToolContext`` instance from ``documents.py``.  Passed through to
        ``safe_child_path`` / ``workspace_root``; typed as ``object`` here so
        this module does not import ``ToolContext`` at module level.
    """
    # Lazy imports — live providers and ToolContext helpers stay out of the
    # module-level import graph (mirrors research_tools.build_live_research_boundary).
    from magi_agent.artifacts.file_delivery import FileDeliveryConfig  # noqa: PLC0415
    from magi_agent.plugins.native._common import (  # noqa: PLC0415
        safe_child_path,
        workspace_root,
    )

    resolved_env: Mapping[str, str] = os.environ if env is None else env

    if not is_live_file_delivery_enabled(resolved_env):
        # ------------------------------------------------------------------ #
        # DEFAULT (gate off): return fake providers — identical to today.     #
        # Fake providers are constructed inline here to avoid a circular      #
        # import between file_delivery_live and documents.                    #
        # ------------------------------------------------------------------ #
        import hashlib as _hashlib  # noqa: PLC0415

        content_digest = "sha256:" + _hashlib.sha256(content_bytes).hexdigest()
        artifact_ref = f"artifact:{_hashlib.sha1(content_digest.encode('utf-8')).hexdigest()[:16]}"
        fake_config = FileDeliveryConfig(
            enabled=True,
            localFakeArtifactServiceEnabled=True,
            localFakeChannelDeliveryEnabled=True,
        )
        return (
            fake_config,
            _FactoryLocalFakeArtifactProvider(
                artifact_ref=artifact_ref,
                content_digest=content_digest,
            ),
            _FactoryLocalFakeChannelProvider(),
        )

    # ---------------------------------------------------------------------- #
    # LIVE path: resolve workspace dirs, construct filesystem providers.      #
    # ---------------------------------------------------------------------- #
    try:
        ws_root = workspace_root(context)  # type: ignore[arg-type]

        artifact_subdir = resolved_env.get(MAGI_FILE_DELIVERY_ARTIFACT_DIR_ENV, "").strip()
        if not artifact_subdir:
            artifact_subdir = _DEFAULT_ARTIFACT_SUBDIR

        outbox_subdir = resolved_env.get(MAGI_FILE_DELIVERY_OUTBOX_DIR_ENV, "").strip()
        if not outbox_subdir:
            outbox_subdir = _DEFAULT_OUTBOX_SUBDIR

        output_dir = safe_child_path(  # type: ignore[arg-type]
            context,
            artifact_subdir,
            default_name=_DEFAULT_ARTIFACT_SUBDIR,
            mutating=True,
            allow_internal=True,
        )
        outbox_dir = safe_child_path(  # type: ignore[arg-type]
            context,
            outbox_subdir,
            default_name=_DEFAULT_OUTBOX_SUBDIR,
            mutating=True,
            allow_internal=True,
        )
        _ = ws_root  # workspace_root validated implicitly via safe_child_path
    except Exception:  # noqa: BLE001
        # Path resolution failed — fall back to fake path (fail-open).
        import hashlib as _hashlib  # noqa: PLC0415

        content_digest = "sha256:" + _hashlib.sha256(content_bytes).hexdigest()
        artifact_ref = f"artifact:{_hashlib.sha1(content_digest.encode('utf-8')).hexdigest()[:16]}"
        fake_config = FileDeliveryConfig(
            enabled=True,
            localFakeArtifactServiceEnabled=True,
            localFakeChannelDeliveryEnabled=True,
        )
        return (
            fake_config,
            _FactoryLocalFakeArtifactProvider(
                artifact_ref=artifact_ref,
                content_digest=content_digest,
            ),
            _FactoryLocalFakeChannelProvider(),
        )

    live_config = FileDeliveryConfig(
        enabled=True,
        liveArtifactStorageEnabled=True,
        liveChannelDeliveryEnabled=True,
    )
    return (
        live_config,
        LiveFilesystemArtifactProvider(
            content_bytes=content_bytes,
            output_dir=output_dir,
            filename=filename,
        ),
        LiveFilesystemChannelProvider(
            content_bytes=content_bytes,
            outbox_dir=outbox_dir,
        ),
    )


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "LIVE_FILE_DELIVERY_ENABLED_ENV",
    "LIVE_FILE_DELIVERY_KILL_SWITCH_ENV",
    "MAGI_FILE_DELIVERY_ARTIFACT_DIR_ENV",
    "MAGI_FILE_DELIVERY_OUTBOX_DIR_ENV",
    "LiveFilesystemArtifactProvider",
    "LiveFilesystemChannelProvider",
    "build_file_delivery_providers",
    "is_live_file_delivery_enabled",
]
