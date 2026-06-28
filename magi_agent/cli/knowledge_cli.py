"""``magi knowledge`` CLI helpers: optional qmd index over the workspace KB.

Parallel to :mod:`magi_agent.cli.memory_cli` but for the first-party knowledge
base (``<workspace>/knowledge/`` + ``.magi/knowledge/``). The native
``KnowledgeSearch`` tool and the dashboard work without qmd (built-in linear
keyword scan); running ``magi knowledge init`` registers the KB subtrees as qmd
collections so search gets BM25 ranking and scale. qmd install + embedding are
reused from :mod:`memory_cli` (they are global, not memory-specific).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class KnowledgeInitReport:
    """Outcome of ``magi knowledge init`` — human-readable lines + flags."""

    qmd_installed: bool = False
    install_method: str | None = None
    collections: list[str] = field(default_factory=list)
    embedded: bool = False
    vector_requested: bool = False
    lines: list[str] = field(default_factory=list)

    def add(self, line: str) -> None:
        self.lines.append(line)


def init_knowledge(*, root: Path, vector: bool) -> KnowledgeInitReport:
    """Install qmd (if missing), register the workspace KB collections, and
    optionally embed for semantic search. Fail-soft throughout.
    """
    from magi_agent.cli import memory_cli  # noqa: PLC0415
    from magi_agent.knowledge.qmd_index import register_knowledge_collections  # noqa: PLC0415

    report = KnowledgeInitReport(vector_requested=vector)

    ok, method = memory_cli.install_qmd()
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
        report.add("knowledge search will use the built-in keyword scan (no qmd).")
        return report

    names = register_knowledge_collections(root)
    report.collections = names
    if names:
        report.add(f"collections: registered {', '.join(names)}")
    else:
        report.add(
            "collections: none registered — add files under "
            "`knowledge/<collection>/` first, then re-run `magi knowledge init`."
        )

    if vector and names:
        embedded = memory_cli.generate_embeddings()
        report.embedded = embedded
        if embedded:
            report.add("embeddings: generated (semantic KB search enabled)")
        else:
            report.add("embeddings: FAILED — `qmd embed` did not complete")

    return report


__all__ = ["KnowledgeInitReport", "init_knowledge"]
