"""Pack signing / digest verification gate (curated trust model "A").

The SAFE OSS foundation of a hosted third-party pack ecosystem: compute a stable
content digest over a discovered pack and, when the operator opts in via
``MAGI_PACK_SIGNING_REQUIRED``, drop any pack whose digest is not in the
``MAGI_TRUSTED_PACK_DIGESTS`` allowlist BEFORE its impl is imported.

Default-OFF is byte-identical: :func:`filter_trusted_packs` returns the input
list object unchanged and never computes a digest when signing is not required.

Bundled FIRST-PARTY packs (``magi_agent/firstparty/packs``, identified by the
first-party pack-id prefix in ``_FIRST_PARTY_PACK_ID_PREFIX``) are trusted by
being bundled and are NEVER dropped by the gate; the allowlist governs only
user/third-party packs.

Distribution (a signed registry, key management, hosted provisioning injection)
is a separate, later, approval-gated effort. This module is the local-runtime
enforcement seam only: a content digest + an allowlist membership test.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from magi_agent.packs.discovery import DiscoveredPack

_LOGGER = logging.getLogger(__name__)

# Mirrors PrimitiveRegistry's first-party origin classification (split literal so
# the brand string is never a contiguous token in source).
_FIRST_PARTY_PACK_ID_PREFIX = "open" "magi."

_DIGEST_ENCODING = "utf-8"
# A field separator that cannot occur in a relative path so the canonical
# serialization is unambiguous (path bytes + content cannot collide across files).
_RECORD_SEPARATOR = b"\x00"


def _referenced_relpaths(pack: "DiscoveredPack") -> list[str]:
    """POSIX relpaths (under ``pack.pack_dir.parent``) of every file the manifest
    references via a ``provides`` impl/spec entry, deduplicated.

    A code ``impl`` entry is ``"module.path:symbol"``; its module file is resolved
    against the search root (``pack_dir.parent``, the same base the loader appends
    to ``sys.path``) trying ``module/path.py`` then ``module/path/__init__.py``.
    A declarative ``spec`` entry is a relpath resolved against the pack dir. Paths
    are made relative to ``pack_dir.parent`` and sorted for determinism. Unknown /
    unresolvable references are skipped (they contribute nothing to the digest;
    the manifest text below still pins the declared ref).
    """
    root = pack.pack_dir.parent
    relpaths: set[str] = set()
    for entry in pack.manifest.provides:
        candidate: Path | None = None
        if entry.spec is not None:
            candidate = (pack.pack_dir / entry.spec).resolve()
        elif entry.impl is not None and ":" in entry.impl:
            module_path = entry.impl.partition(":")[0]
            parts = module_path.split(".")
            module_file = root.joinpath(*parts).with_suffix(".py")
            package_init = root.joinpath(*parts) / "__init__.py"
            if module_file.is_file():
                candidate = module_file.resolve()
            elif package_init.is_file():
                candidate = package_init.resolve()
        if candidate is None or not candidate.is_file():
            continue
        try:
            relpaths.add(candidate.relative_to(root.resolve()).as_posix())
        except ValueError:
            # Outside the search root (symlink escape etc.); skip from the digest.
            continue
    return sorted(relpaths)


def compute_pack_digest(pack: "DiscoveredPack") -> str:
    """Return a stable sha256 hex digest over a canonical pack serialization.

    The serialization is the ``pack.toml`` bytes followed by, for every
    impl/spec file the manifest references (in sorted relpath order), the file's
    POSIX relpath and raw bytes, each framed by a NUL record separator. Sorting +
    fixed framing makes the digest deterministic and content-sensitive: editing
    the manifest OR any referenced impl/spec file changes the digest.
    """
    hasher = hashlib.sha256()
    try:
        hasher.update(b"pack.toml")
        hasher.update(_RECORD_SEPARATOR)
        hasher.update(pack.path.read_bytes())
    except OSError:
        # An unreadable manifest cannot be trusted; fold the pack id in so the
        # digest stays defined (and will simply not match any real allowlist).
        hasher.update(pack.manifest.pack_id.encode(_DIGEST_ENCODING))
    for relpath in _referenced_relpaths(pack):
        hasher.update(_RECORD_SEPARATOR)
        hasher.update(relpath.encode(_DIGEST_ENCODING))
        hasher.update(_RECORD_SEPARATOR)
        try:
            hasher.update((pack.pack_dir.parent / relpath).read_bytes())
        except OSError:
            hasher.update(relpath.encode(_DIGEST_ENCODING))
    return hasher.hexdigest()


def pack_digest_trusted(digest: str, trusted: frozenset[str]) -> bool:
    """True iff ``digest`` (casefolded) is in the operator ``trusted`` allowlist."""
    return digest.casefold() in trusted


def _is_first_party(pack: "DiscoveredPack") -> bool:
    return pack.manifest.pack_id.startswith(_FIRST_PARTY_PACK_ID_PREFIX)


def filter_trusted_packs(
    enabled: "list[DiscoveredPack]",
    *,
    env: "dict[str, str] | None" = None,
) -> "list[DiscoveredPack]":
    """Drop untrusted user packs when the signing gate is required.

    When ``MAGI_PACK_SIGNING_REQUIRED`` is OFF this returns the SAME list object
    untouched and computes no digest (byte-identical to today). When ON, each
    non-first-party pack whose content digest is absent from
    ``MAGI_TRUSTED_PACK_DIGESTS`` is dropped (a warning naming pack_id + digest is
    logged). Bundled first-party packs (the first-party pack-id prefix) are
    always kept.

    Order is preserved so the downstream last-wins / base-precedence contract is
    unchanged for the packs that survive.
    """
    from magi_agent.config.env import (  # noqa: PLC0415
        pack_signing_required,
        trusted_pack_digests,
    )

    if not pack_signing_required(env):
        return enabled
    trusted = trusted_pack_digests(env)
    kept: list["DiscoveredPack"] = []
    for pack in enabled:
        if _is_first_party(pack):
            kept.append(pack)
            continue
        digest = compute_pack_digest(pack)
        if pack_digest_trusted(digest, trusted):
            kept.append(pack)
            continue
        _LOGGER.warning(
            "pack signing required: dropping untrusted pack %s (digest %s)",
            pack.manifest.pack_id,
            digest,
        )
    return kept


__all__ = [
    "compute_pack_digest",
    "pack_digest_trusted",
    "filter_trusted_packs",
]
