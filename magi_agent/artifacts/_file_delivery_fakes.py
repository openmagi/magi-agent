"""Shared local-fake provider implementations for the FileDelivery boundary.

This leaf module is the **single source of truth** for the two local-fake
providers that are used when file delivery runs in default-OFF (fake) mode.
Both ``magi_agent.plugins.native.documents`` and
``magi_agent.artifacts.file_delivery_live`` import from here so the
implementations can never drift apart.

Import constraints
------------------
*   No top-level network imports (``requests``, ``httpx``, ``urllib``,
    ``socket``, etc.).  Only stdlib + ``magi_agent.channels.contract`` and
    ``magi_agent.artifacts.file_delivery`` type imports are permitted here.
*   This module MUST NOT import ``magi_agent.plugins.native.documents`` or
    ``magi_agent.artifacts.file_delivery_live`` (no circular imports).
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from magi_agent.channels.contract import ChannelDeliveryReceipt, ChannelRef


# ---------------------------------------------------------------------------
# Shared digest helper
# ---------------------------------------------------------------------------


def _short_digest(value: str) -> str:
    """Return the first 16 hex characters of the SHA-1 digest of ``value``."""
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Local-fake providers
# ---------------------------------------------------------------------------


class LocalFakeFileArtifactProvider:
    """Fake (no-op) file artifact provider for default-OFF / test mode.

    Carries ``openmagi_local_fake_provider = True`` so the boundary's
    ``_is_trusted_fake_provider()`` gate admits it through the local-fake path.
    """

    openmagi_local_fake_provider: bool = True

    def __init__(self, *, artifact_ref: str, content_digest: str) -> None:
        self._artifact_ref = artifact_ref
        self._content_digest = content_digest

    def write_artifact(self, request: Any) -> Mapping[str, object]:
        request_id: str = getattr(request, "request_id", "unknown")
        return {
            "status": "ok",
            "artifactRef": self._artifact_ref,
            "contentDigest": self._content_digest,
            "receiptId": f"artifact-receipt:{_short_digest(request_id)}",
        }


class LocalFakeChannelDeliveryProvider:
    """Fake (no-op) channel delivery provider for default-OFF / test mode.

    Carries ``openmagi_local_fake_provider = True`` so the boundary's
    ``_is_trusted_fake_provider()`` gate admits it through the local-fake path.
    """

    openmagi_local_fake_provider: bool = True

    def deliver(self, request: Any) -> ChannelDeliveryReceipt:
        channel: ChannelRef | None = getattr(request, "channel", None)
        if channel is None:
            raise ValueError("channel_required")
        request_id: str = getattr(request, "request_id", "unknown")
        artifact_refs: tuple[str, ...] = getattr(request, "artifact_refs", ())
        file_refs: tuple[str, ...] = getattr(request, "file_refs", ())
        return ChannelDeliveryReceipt(
            receiptId=f"receipt:{_short_digest(request_id)}",
            requestId=request_id,
            channel=channel,
            status="sent",
            providerMessageId=f"message:{_short_digest(request_id + ':message')}",
            artifactRefs=artifact_refs,
            fileRefs=file_refs,
        )


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "LocalFakeChannelDeliveryProvider",
    "LocalFakeFileArtifactProvider",
    "_short_digest",
]
