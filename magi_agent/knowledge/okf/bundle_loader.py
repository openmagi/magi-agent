"""OKF bundle loader (PR1, pure + unwired).

Walks one or more OKF bundle roots (nested folders of ``*.md`` with YAML
frontmatter), validates the required ``type`` field, and returns a read-only
:class:`OkfBundleIndex` of :class:`OkfDoc` records.  Trusted content is parsed
WITHOUT redaction (the design rejects routing OKF through the fake-provider
KnowledgeBoundary).

Safety:
  * A dedicated :func:`_resolve_okf_path` confines every candidate to its bundle
    root via ``os.path.realpath`` (blocking ``..`` / symlink escape).
  * Secret/sealed basenames are skipped using a LOCAL copy of the rules (mirrors
    ``transport/app_api.py:42-52`` — copied, not imported, to keep ``knowledge``
    decoupled from ``transport``).

Caps (never silent):
  * Per-doc body byte cap (``config.max_doc_bytes``) → truncate body, mark
    ``truncated=True``.
  * Global ``max_docs`` / ``max_total_bytes`` → stop loading, count drops.

An in-memory cache keyed by ``(path, mtime, size)`` avoids re-parsing unchanged
files across calls.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from magi_agent.knowledge.okf.config import MAX_DOC_BYTES, OkfConfig
from magi_agent.knowledge.okf.matcher import match_score

logger = logging.getLogger(__name__)

# Frontmatter block: leading ``---\n ... \n---`` (DOTALL for multi-line YAML).
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)

# ---------------------------------------------------------------------------
# Secret / sealed basename rules — LOCAL copy mirroring
# ``magi_agent/transport/app_api.py:42-52``.  Re-declared here (not imported) so
# the knowledge subsystem never takes a dependency on transport.
# ---------------------------------------------------------------------------
_SEALED_BASENAMES = {
    "SOUL.md",
    "TOOLS.md",
    "AGENTS.md",
    "CLAUDE.md",
    "HEARTBEAT.md",
}
_SECRET_NAME_RE = re.compile(
    r"(^\.env)|secret|credential|password|api[_-]?key|token", re.IGNORECASE
)


def _is_protected(path: Path) -> bool:
    name = path.name
    return name in _SEALED_BASENAMES or bool(_SECRET_NAME_RE.search(name))


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OkfDoc:
    """First-class internal representation of one OKF document.

    Not bound to any boundary contract — trusted content, kept verbatim.
    """

    rel_path: str
    bundle_root: str
    doc_type: str
    title: str
    description: str
    tags: tuple[str, ...]
    resource: str | None
    frontmatter: Mapping[str, object]
    body: str
    content_digest: str
    byte_size: int
    truncated: bool


@dataclass(frozen=True)
class OkfBundleIndex:
    """Read-only result of loading one or more bundles."""

    docs: tuple[OkfDoc, ...]
    skipped_no_type: int = 0
    skipped_unsafe: int = 0
    dropped_capped: int = 0

    def search(self, query: str, *, max_records: int) -> list[OkfDoc]:
        """Lexical search over title/description/tags/body (highest score first)."""
        scored: list[tuple[int, int, OkfDoc]] = []
        for order, doc in enumerate(self.docs):
            score = match_score(
                query,
                doc.title,
                doc.description,
                " ".join(doc.tags),
                doc.body,
            )
            if score > 0:
                # Stable: higher score first, then original load order.
                scored.append((score, order, doc))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [doc for _, _, doc in scored[: max(0, max_records)]]


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def _resolve_okf_path(bundle_root: Path, candidate: Path) -> Path | None:
    """Resolve ``candidate`` and confirm it stays inside ``bundle_root``.

    Returns the real resolved path, or ``None`` when the candidate escapes the
    bundle (``..`` / symlink) or carries a secret/sealed basename.
    """
    root_real = os.path.realpath(bundle_root.resolve())
    cand_real = os.path.realpath(candidate)
    # Containment: the real path must be the root itself or a descendant.
    if cand_real != root_real and not cand_real.startswith(root_real + os.sep):
        return None
    resolved = Path(cand_real)
    if _is_protected(resolved):
        return None
    return resolved


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


def _normalize_tags(raw: object) -> tuple[str, ...]:
    if isinstance(raw, (list, tuple)):
        return tuple(str(t).strip() for t in raw if str(t).strip())
    if isinstance(raw, str):
        return tuple(part.strip() for part in raw.split(",") if part.strip())
    return ()


def _title_fallback(frontmatter: Mapping[str, object], body: str, stem: str) -> str:
    title = frontmatter.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    h1 = _H1_RE.search(body)
    if h1:
        return h1.group(1).strip()
    return stem


def _parse_doc(
    *,
    resolved: Path,
    bundle_root: Path,
    raw_bytes: bytes,
    config: OkfConfig,
) -> OkfDoc | None:
    """Parse one file's bytes into an :class:`OkfDoc`, or ``None`` (skip).

    ``None`` here means "no frontmatter / not a dict / missing type" — the caller
    counts it as ``skipped_no_type``.
    """
    text = raw_bytes.decode("utf-8", errors="replace")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None
    try:
        loaded = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None
    if not isinstance(loaded, Mapping):
        return None
    doc_type = loaded.get("type")
    if not isinstance(doc_type, str) or not doc_type.strip():
        return None

    body = text[match.end():]
    body_bytes = body.encode("utf-8")
    max_doc_bytes = config.max_doc_bytes or MAX_DOC_BYTES
    truncated = False
    if len(body_bytes) > max_doc_bytes:
        body = body_bytes[:max_doc_bytes].decode("utf-8", errors="ignore")
        truncated = True

    description = loaded.get("description")
    resource = loaded.get("resource")
    rel_path = resolved.relative_to(bundle_root.resolve()).as_posix()

    return OkfDoc(
        rel_path=rel_path,
        bundle_root=str(bundle_root),
        doc_type=doc_type.strip(),
        title=_title_fallback(loaded, body, resolved.stem),
        description=description.strip() if isinstance(description, str) else "",
        tags=_normalize_tags(loaded.get("tags")),
        resource=resource if isinstance(resource, str) and resource.strip() else None,
        frontmatter=dict(loaded),
        body=body,
        content_digest=hashlib.sha256(raw_bytes).hexdigest(),
        byte_size=len(raw_bytes),
        truncated=truncated,
    )


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

#: Keyed by (absolute path, mtime_ns, size) → parsed doc. Invalidated naturally:
#: a changed file produces a different key, so the stale entry is simply unused.
_DOC_CACHE: dict[tuple[str, int, int], OkfDoc] = {}


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_bundles(
    bundle_roots: Iterable[str | Path],
    *,
    config: OkfConfig,
) -> OkfBundleIndex:
    """Load every ``*.md`` under each bundle root into an :class:`OkfBundleIndex`.

    Enforces global caps (``max_docs`` / ``max_total_bytes``): once a cap is hit
    the remaining candidates are counted in ``dropped_capped`` rather than loaded.
    """
    docs: list[OkfDoc] = []
    skipped_no_type = 0
    skipped_unsafe = 0
    dropped_capped = 0
    total_bytes = 0

    for raw_root in bundle_roots:
        root = Path(raw_root)
        if not root.is_dir():
            continue
        # Deterministic order so caps drop a stable tail.
        for candidate in sorted(root.rglob("*.md")):
            resolved = _resolve_okf_path(root, candidate)
            if resolved is None:
                skipped_unsafe += 1
                continue
            if not resolved.is_file():
                continue

            try:
                stat = resolved.stat()
            except OSError:
                skipped_unsafe += 1
                continue

            # Global caps: stop loading but keep counting drops.
            if len(docs) >= config.max_docs:
                dropped_capped += 1
                continue
            if total_bytes + stat.st_size > config.max_total_bytes:
                dropped_capped += 1
                continue

            cache_key = (str(resolved), stat.st_mtime_ns, stat.st_size)
            doc = _DOC_CACHE.get(cache_key)
            if doc is None:
                try:
                    raw_bytes = resolved.read_bytes()
                except OSError:
                    skipped_unsafe += 1
                    continue
                doc = _parse_doc(
                    resolved=resolved,
                    bundle_root=root,
                    raw_bytes=raw_bytes,
                    config=config,
                )
                if doc is None:
                    skipped_no_type += 1
                    continue
                _DOC_CACHE[cache_key] = doc

            docs.append(doc)
            total_bytes += stat.st_size

    if dropped_capped:
        logger.warning(
            "okf: dropped %d document(s) after hitting global caps "
            "(max_docs=%d, max_total_bytes=%d)",
            dropped_capped,
            config.max_docs,
            config.max_total_bytes,
        )

    return OkfBundleIndex(
        docs=tuple(docs),
        skipped_no_type=skipped_no_type,
        skipped_unsafe=skipped_unsafe,
        dropped_capped=dropped_capped,
    )


__all__ = [
    "OkfBundleIndex",
    "OkfDoc",
    "load_bundles",
]
