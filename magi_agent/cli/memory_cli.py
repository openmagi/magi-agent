"""``magi memory`` CLI helpers: optional qmd install + explicit search.

WHY THIS EXISTS
---------------
The Hipocampus memory recall path uses a pluggable
:class:`magi_agent.memory.search.SearchBackend`.  The zero-dependency default is
pure-Python BM25 (:class:`PyBM25Backend`); when the external ``qmd`` binary is on
PATH the selector upgrades to :class:`QmdBackend` (still BM25 on the hot path).

``qmd`` is intentionally NOT a hard runtime dependency (no pip/brew/npm coupling
in the package): a fresh install searches memory with no binary present.  This
module is the OPT-IN bridge an operator runs once to (a) install ``qmd``,
(b) register the workspace ``memory/`` tree as a per-workspace collection so
search is not silently empty, and (c) — only with ``--vector`` — generate vector
embeddings so the explicit search surfaces can run semantic ``qmd vsearch``.

GOVERNANCE
----------
Nothing here runs implicitly.  The per-turn recall hot path NEVER calls into this
module and NEVER uses vector search (``qmd vsearch`` cold-loads the embedding
model, ~10-40s — unacceptable per-turn).  Vector is reserved for the explicit,
latency-tolerant surfaces (``magi memory search --vector`` and the dashboard
endpoint), gated behind the ``vector_search`` opt-in this module writes.

The ``subprocess`` / ``shutil`` seams live here (mirroring
:mod:`magi_agent.memory.search.qmd`) so the boundary-guarded memory modules stay
process-free.  All installer steps are fail-soft and reported, never raised.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

#: npm package that provides the ``qmd`` binary (the canonical distribution).
_QMD_NPM_PACKAGE = "@tobilu/qmd"

#: Generous cap: installs and embedding can be slow (model download ~2GB).
_INSTALL_TIMEOUT_SECONDS = 1800
_EMBED_TIMEOUT_SECONDS = 1800


@dataclass
class MemoryInitReport:
    """Outcome of ``magi memory init`` — a sequence of human-readable lines."""

    qmd_installed: bool = False
    install_method: str | None = None
    collection_registered: bool = False
    collection_name: str | None = None
    embedded: bool = False
    vector_requested: bool = False
    config_path: str | None = None
    lines: list[str] = field(default_factory=list)

    def add(self, line: str) -> None:
        self.lines.append(line)


def qmd_available() -> bool:
    """True when the ``qmd`` binary is resolvable on PATH."""
    return shutil.which("qmd") is not None


def qmd_version() -> str | None:
    """Return the ``qmd --version`` string, or ``None`` if unavailable."""
    if not qmd_available():
        return None
    completed = _run(["qmd", "--version"], timeout=30)
    if completed is None or completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _run(args: list[str], *, timeout: int) -> subprocess.CompletedProcess[str] | None:
    """Fail-soft subprocess wrapper. Returns ``None`` when the tool is absent or
    the call errors (missing binary, non-zero handled by caller, timeout, OSError).
    """
    if shutil.which(args[0]) is None:
        return None
    try:
        return subprocess.run(  # noqa: S603 - fixed arg list, never shell
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def install_qmd() -> tuple[bool, str]:
    """Install the ``qmd`` binary if missing.

    Tries Homebrew first (``brew install qmd``) then npm
    (``npm install -g @tobilu/qmd``); whichever package manager is present and
    succeeds wins.  Returns ``(success, method)`` where ``method`` describes what
    happened (``"already-present"``, ``"brew"``, ``"npm"``, or a failure reason).
    Never raises.
    """
    if qmd_available():
        return True, "already-present"

    if shutil.which("brew") is not None:
        completed = _run(["brew", "install", "qmd"], timeout=_INSTALL_TIMEOUT_SECONDS)
        if completed is not None and completed.returncode == 0 and qmd_available():
            return True, "brew"

    if shutil.which("npm") is not None:
        completed = _run(
            ["npm", "install", "-g", _QMD_NPM_PACKAGE], timeout=_INSTALL_TIMEOUT_SECONDS
        )
        if completed is not None and completed.returncode == 0 and qmd_available():
            return True, "npm"

    if shutil.which("brew") is None and shutil.which("npm") is None:
        return False, "no-package-manager"
    return False, "install-failed"


def register_collection(root: Path) -> str | None:
    """Register the workspace ``memory/`` tree as a per-workspace qmd collection.

    Uses :meth:`QmdBackend.reindex` with the explicit auto-register opt-in so a
    brand-new collection IS created (the default per-turn path never does this —
    multi-tenant safety).  Returns the collection name on success, else ``None``
    (qmd absent, no ``memory/`` dir yet, or registration failed).
    """
    if not qmd_available():
        return None
    from magi_agent.memory.search.qmd import QmdBackend, collection_name_for  # noqa: PLC0415

    memory_dir = root / "memory"
    if not memory_dir.is_dir():
        return None
    backend = QmdBackend()
    backend.reindex(root, allow_auto_register=True)
    return collection_name_for(memory_dir.resolve())


def generate_embeddings() -> bool:
    """Run ``qmd embed`` to generate vector embeddings (first run downloads the
    embedding model, ~2GB).  Global to all qmd collections by design of the CLI.
    Returns True on success, False otherwise. Never raises.
    """
    if not qmd_available():
        return False
    completed = _run(["qmd", "embed"], timeout=_EMBED_TIMEOUT_SECONDS)
    return completed is not None and completed.returncode == 0


def write_memory_opt_ins(*, vector: bool) -> Path:
    """Merge memory opt-ins into ``~/.magi/config.toml`` ``[memory]`` and return
    the config path.

    Always sets ``prefer_qmd = true`` (use qmd when present).  With ``vector`` it
    also sets ``vector_search = true`` so the explicit search surfaces may run
    ``qmd vsearch``.  Existing keys/sections are preserved (read-merge-write via
    the providers TOML helpers).
    """
    from magi_agent.cli import providers as _providers  # noqa: PLC0415

    config = dict(_providers._load_config_file())
    memory = dict(config.get("memory", {})) if isinstance(config.get("memory"), dict) else {}
    memory["prefer_qmd"] = True
    if vector:
        memory["vector_search"] = True
    config["memory"] = memory

    path = _providers._config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_providers._render_toml(config), encoding="utf-8")
    return path


def init_memory(*, root: Path, vector: bool) -> MemoryInitReport:
    """Orchestrate ``magi memory init``: install qmd, register the workspace
    collection, optionally embed, and persist the opt-ins.  Fail-soft throughout.
    """
    report = MemoryInitReport(vector_requested=vector)

    ok, method = install_qmd()
    report.qmd_installed = ok
    report.install_method = method
    if ok:
        report.add(f"qmd: OK ({method})")
    else:
        hint = (
            "install Homebrew or Node/npm first"
            if method == "no-package-manager"
            else "see `brew install qmd` / `npm install -g @tobilu/qmd`"
        )
        report.add(f"qmd: NOT INSTALLED ({method}) — {hint}")
        report.add("memory search will use the built-in BM25 backend (no qmd).")
        # Still persist prefer_qmd so a later qmd install is picked up.
        report.config_path = str(write_memory_opt_ins(vector=vector))
        report.add(f"config: wrote opt-ins to {report.config_path}")
        return report

    name = register_collection(root)
    report.collection_registered = name is not None
    report.collection_name = name
    if name is not None:
        report.add(f"collection: registered {name} ({root / 'memory'})")
    else:
        report.add(
            "collection: not registered — create a memory/ directory first, "
            "then re-run `magi memory init`."
        )

    if vector:
        embedded = generate_embeddings()
        report.embedded = embedded
        if embedded:
            report.add("embeddings: generated (qmd vsearch enabled for explicit search)")
        else:
            report.add("embeddings: FAILED — `qmd embed` did not complete; vector search unavailable")

    report.config_path = str(write_memory_opt_ins(vector=vector))
    report.add(f"config: wrote opt-ins to {report.config_path}")
    return report


def search_memory(
    *, root: Path, query: str, vector: bool, k: int
) -> list[tuple[str, float, str]]:
    """Explicit memory search. Returns ``(path, score, snippet)`` tuples.

    When ``vector`` is set AND the ``vector_search`` opt-in is on AND qmd is
    present, runs semantic ``qmd vsearch``; otherwise BM25 (qmd ``search`` or the
    pure-Python backend).  Fail-soft: any backend error yields ``[]``.
    """
    try:
        from magi_agent.memory.config import resolve_memory_config  # noqa: PLC0415
        from magi_agent.memory.search import select_search_backend  # noqa: PLC0415

        backend = select_search_backend(resolve_memory_config(), vector=vector)
        backend.reindex(root)
        hits = backend.search(query, k=max(int(k), 1))
    except Exception:  # noqa: BLE001 - never raise out of the CLI command
        return []
    out: list[tuple[str, float, str]] = []
    for hit in hits:
        path = getattr(hit, "path", None)
        content = getattr(hit, "content", None)
        score = getattr(hit, "score", None)
        if not isinstance(path, str) or not isinstance(content, str):
            continue
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            continue
        snippet = content.replace("\n", " ").strip()[:160]
        out.append((path, float(score), snippet))
    return out


__all__ = [
    "MemoryInitReport",
    "generate_embeddings",
    "init_memory",
    "install_qmd",
    "qmd_available",
    "qmd_version",
    "register_collection",
    "search_memory",
    "write_memory_opt_ins",
]
