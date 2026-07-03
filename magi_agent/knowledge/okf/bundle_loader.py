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

#: Default ``type`` assigned to a doc rescued by ``config.auto_type`` (a doc with
#: no frontmatter / missing / non-string type). Fixed + deterministic (no LLM).
_DEFAULT_DOC_TYPE = "document"

#: Directory names pruned from the walk so OKF never intersects the memory
#: subsystem (``memory/``), the ``.magi`` identity namespace, or VCS / dependency
#: internals (``.git`` / ``node_modules``).  Matters when the scope is widened to
#: the whole ``knowledge/`` dir (design §2 Phase 2 SHOULD-FIX-1); harmless for the
#: narrow ``knowledge/okf`` root (which contains none of these).  These are
#: OUT-OF-SCOPE, not unsafe, so they are counted separately in ``pruned``.
_PRUNE_DIR_NAMES = frozenset({"memory", ".magi", ".git", "node_modules"})

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


def _is_pruned(root: Path, candidate: Path) -> bool:
    """True when ``candidate`` lives under a pruned dir relative to ``root``.

    Any path segment (except the final basename) matching ``_PRUNE_DIR_NAMES`` is
    excluded, so e.g. ``knowledge/memory/x.md`` and ``knowledge/a/.git/y.md`` are
    both pruned.  A candidate not under ``root`` (should not happen) is treated as
    not pruned; the downstream ``_resolve_okf_path`` containment check handles it.
    """
    try:
        rel = candidate.relative_to(root)
    except ValueError:
        return False
    return any(part in _PRUNE_DIR_NAMES for part in rel.parts[:-1])


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
    #: Candidates skipped because they live under a pruned directory
    #: (``memory`` / ``.magi`` / ``.git`` / ``node_modules``). Out-of-scope, NOT
    #: unsafe — counted separately so widening the scope leaves no silent drops.
    pruned: int = 0
    #: Docs indexed via the auto-type path (would have been ``skipped_no_type``
    #: under strict mode, rescued because ``config.auto_type`` was ON). Observability
    #: counter so ``skipped_no_type`` going to 0 does not hide the transition.
    auto_typed: int = 0

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
) -> tuple[OkfDoc | None, bool]:
    """Parse one file's bytes into an :class:`OkfDoc`.

    Returns ``(doc, auto_typed)``:
      * ``(None, False)`` → skip (caller counts ``skipped_no_type``). This means
        "no frontmatter / not a dict / missing/non-string type" under strict mode
        (``config.auto_type`` False), or broken/non-dict YAML under any mode.
      * ``(doc, False)`` → indexed with an explicit valid ``type``.
      * ``(doc, True)`` → indexed via the auto-type path (``config.auto_type`` True
        rescued a doc that strict mode would have skipped).
    """
    text = raw_bytes.decode("utf-8", errors="replace")
    match = _FRONTMATTER_RE.match(text)

    # Track whether this doc had to be rescued by auto_type.
    auto_typed = False

    if not match:
        # No frontmatter. Strict mode skips; auto_type indexes as ``document``.
        if not config.auto_type:
            return None, False
        loaded: Mapping[str, object] = {}
        doc_type = _DEFAULT_DOC_TYPE
        auto_typed = True
    else:
        try:
            parsed = yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            return None, False
        # Broken/non-dict frontmatter is never trusted, even under auto_type.
        if not isinstance(parsed, Mapping):
            return None, False
        loaded = parsed
        raw_type = loaded.get("type")
        if isinstance(raw_type, str) and raw_type.strip():
            doc_type = raw_type.strip()
        elif config.auto_type:
            # Missing / empty / non-string type → treat as malformed, default.
            doc_type = _DEFAULT_DOC_TYPE
            auto_typed = True
        else:
            return None, False

    # BODY-SLICE FIX: ``match.end()`` raises when ``match is None``. Defensive in
    # both paths (in the OFF path match is always non-None here, but be explicit).
    body = text[match.end():] if match else text
    body_bytes = body.encode("utf-8")
    max_doc_bytes = config.max_doc_bytes or MAX_DOC_BYTES
    truncated = False
    if len(body_bytes) > max_doc_bytes:
        body = body_bytes[:max_doc_bytes].decode("utf-8", errors="ignore")
        truncated = True

    description = loaded.get("description")
    resource = loaded.get("resource")
    rel_path = resolved.relative_to(bundle_root.resolve()).as_posix()

    doc = OkfDoc(
        rel_path=rel_path,
        bundle_root=str(bundle_root),
        doc_type=doc_type,
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
    return doc, auto_typed


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

#: Keyed by (absolute path, mtime_ns, size) → (parsed doc, auto_typed flag).
#: Invalidated naturally: a changed file produces a different key, so the stale
#: entry is simply unused. The auto_typed flag is cached alongside the doc so the
#: observability counter is consistent across cache hits. The cache key does NOT
#: include ``config.auto_type``; a doc that was strict-skipped (returned None) is
#: never cached, so flipping auto_type ON re-parses it, and a doc cached with an
#: explicit valid type is auto_type-independent (its ``doc_type`` never changes).
_DOC_CACHE: dict[tuple[str, int, int], tuple[OkfDoc, bool]] = {}


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
    auto_typed = 0
    pruned = 0
    total_bytes = 0

    for raw_root in bundle_roots:
        root = Path(raw_root)
        if not root.is_dir():
            continue
        # Deterministic order so caps drop a stable tail.
        for candidate in sorted(root.rglob("*.md")):
            # Prune out-of-scope dirs (memory/.magi/.git/node_modules) before any
            # I/O so a widened knowledge/ root never intersects them.
            if _is_pruned(root, candidate):
                pruned += 1
                continue
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
            cached = _DOC_CACHE.get(cache_key)
            if cached is None:
                try:
                    raw_bytes = resolved.read_bytes()
                except OSError:
                    skipped_unsafe += 1
                    continue
                doc, was_auto_typed = _parse_doc(
                    resolved=resolved,
                    bundle_root=root,
                    raw_bytes=raw_bytes,
                    config=config,
                )
                if doc is None:
                    skipped_no_type += 1
                    continue
                _DOC_CACHE[cache_key] = (doc, was_auto_typed)
            else:
                doc, was_auto_typed = cached

            docs.append(doc)
            if was_auto_typed:
                auto_typed += 1
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
        auto_typed=auto_typed,
        pruned=pruned,
    )


__all__ = [
    "OkfBundleIndex",
    "OkfDoc",
    "_DEFAULT_DOC_TYPE",
    "load_bundles",
]
