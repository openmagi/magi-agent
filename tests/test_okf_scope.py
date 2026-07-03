"""PR2 — OKF scope expansion: config.default_scope + bundle-root resolution + prune.

Phase 2 of the zero-friction KB design.  Widens the OKF fallback bundle root from
``<workspace>/knowledge/okf`` to the whole ``<workspace>/knowledge`` when
explicitly opted-in via ``MAGI_KNOWLEDGE_OKF_SCOPE=knowledge_root``.  The scope is
opt-in (does NOT follow the master) so existing OKF users' search surface never
changes silently.  When widened, the walk must prune ``memory/`` / ``.magi/`` /
``.git/`` / ``node_modules/`` so OKF never intersects the memory subsystem or
secrets/VCS internals.

Hermetic: config-resolver tests pass an explicit ``env=`` dict; bundle-root and
prune tests build a workspace under ``tmp_path`` and a ``ToolContext`` exactly like
``tests/test_okf_lookup_tool.py``.
"""
from __future__ import annotations

from pathlib import Path

from magi_agent.knowledge.okf.bundle_loader import load_bundles
from magi_agent.knowledge.okf.config import OkfConfig, resolve_okf_config
from magi_agent.plugins.native.okf import _resolve_bundle_roots
from magi_agent.tools.context import ToolContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _context(workspace_root: str | None = None) -> ToolContext:
    return ToolContext(
        bot_id="test-bot",
        session_id="session-a",
        session_key="session-a",
        workspace_root=workspace_root,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# config resolver — default_scope
# ---------------------------------------------------------------------------


def test_default_scope_is_okf_subdir_when_nothing_set() -> None:
    cfg = resolve_okf_config(env={}, config={})
    assert cfg.default_scope == "okf_subdir"


def test_scope_env_knowledge_root() -> None:
    cfg = resolve_okf_config(
        env={"MAGI_KNOWLEDGE_OKF_SCOPE": "knowledge_root"}, config={}
    )
    assert cfg.default_scope == "knowledge_root"


def test_scope_config_key_knowledge_root() -> None:
    cfg = resolve_okf_config(
        env={}, config={"knowledge_okf": {"default_scope": "knowledge_root"}}
    )
    assert cfg.default_scope == "knowledge_root"


def test_scope_garbage_falls_back_to_okf_subdir() -> None:
    cfg = resolve_okf_config(
        env={"MAGI_KNOWLEDGE_OKF_SCOPE": "banana"}, config={}
    )
    assert cfg.default_scope == "okf_subdir"


def test_scope_does_not_follow_master() -> None:
    # Master ON, no explicit scope override → scope stays narrow (opt-in only).
    cfg = resolve_okf_config(
        env={"MAGI_KNOWLEDGE_OKF_ENABLED": "1"}, config={}
    )
    assert cfg.master_enabled is True
    assert cfg.default_scope == "okf_subdir"


# ---------------------------------------------------------------------------
# _resolve_bundle_roots — scope-driven fallback
# ---------------------------------------------------------------------------


def test_scope_okf_subdir_resolves_knowledge_okf_only(tmp_path) -> None:
    (tmp_path / "knowledge" / "okf").mkdir(parents=True)
    _write(tmp_path / "knowledge" / "foo.md", "# foo\n")
    cfg = OkfConfig(defaultScope="okf_subdir")

    roots = _resolve_bundle_roots(cfg, _context(str(tmp_path)))

    assert roots == [tmp_path / "knowledge" / "okf"]


def test_scope_knowledge_root_resolves_whole_knowledge_dir(tmp_path) -> None:
    (tmp_path / "knowledge" / "okf").mkdir(parents=True)
    _write(tmp_path / "knowledge" / "foo.md", "# foo\n")
    cfg = OkfConfig(defaultScope="knowledge_root")

    roots = _resolve_bundle_roots(cfg, _context(str(tmp_path)))

    assert roots == [tmp_path / "knowledge"]


def test_explicit_bundle_paths_win_over_scope(tmp_path) -> None:
    explicit = tmp_path / "custom" / "bundles"
    explicit.mkdir(parents=True)
    (tmp_path / "knowledge" / "okf").mkdir(parents=True)
    cfg = OkfConfig(
        bundlePaths=(str(explicit),), defaultScope="knowledge_root"
    )

    roots = _resolve_bundle_roots(cfg, _context(str(tmp_path)))

    assert roots == [explicit]


def test_fallback_absent_dir_is_empty_okf_subdir(tmp_path) -> None:
    cfg = OkfConfig(defaultScope="okf_subdir")
    roots = _resolve_bundle_roots(cfg, _context(str(tmp_path)))
    assert roots == []


def test_fallback_absent_dir_is_empty_knowledge_root(tmp_path) -> None:
    cfg = OkfConfig(defaultScope="knowledge_root")
    roots = _resolve_bundle_roots(cfg, _context(str(tmp_path)))
    assert roots == []


# ---------------------------------------------------------------------------
# prune — widened knowledge/ root excludes memory/.magi/.git/node_modules
# ---------------------------------------------------------------------------


def test_prune_excludes_memory_git_node_modules(tmp_path) -> None:
    knowledge = tmp_path / "knowledge"
    _write(knowledge / "note.md", "# note\n\nplain note under knowledge root.")
    _write(knowledge / "okf" / "orders.md", "# orders\n\norders table body.")
    _write(knowledge / "memory" / "secret.md", "# secret\n\nmemory subsystem file.")
    _write(knowledge / ".git" / "x.md", "# x\n\nvcs internal.")
    _write(knowledge / "node_modules" / "y.md", "# y\n\nvendored dep.")
    # auto_type ON so plain markdown (no frontmatter) is indexed.
    cfg = OkfConfig(
        masterEnabled=True,
        lookupEnabled=True,
        autoType=True,
        defaultScope="knowledge_root",
    )

    index = load_bundles([knowledge], config=cfg)

    rel_paths = {doc.rel_path for doc in index.docs}
    assert rel_paths == {"note.md", "okf/orders.md"}
    # memory/.git/node_modules pruned → counted, not silently dropped.
    assert index.pruned == 3


def test_prune_excludes_dot_magi(tmp_path) -> None:
    knowledge = tmp_path / "knowledge"
    _write(knowledge / "note.md", "# note\n\nplain note.")
    _write(knowledge / ".magi" / "identity.md", "# identity\n\nmagi namespace file.")
    cfg = OkfConfig(
        masterEnabled=True,
        lookupEnabled=True,
        autoType=True,
        defaultScope="knowledge_root",
    )

    index = load_bundles([knowledge], config=cfg)

    rel_paths = {doc.rel_path for doc in index.docs}
    assert rel_paths == {"note.md"}
    assert index.pruned == 1


def test_prune_counter_zero_for_narrow_okf_root(tmp_path) -> None:
    # The narrow knowledge/okf root has no prune-set dirs → pruned stays 0.
    okf = tmp_path / "knowledge" / "okf"
    _write(okf / "orders.md", "type: table\ntitle: Orders")
    _write(
        okf / "guide.md",
        "---\ntype: guide\ntitle: Guide\n---\n# Guide\n\nbody.",
    )
    cfg = OkfConfig(masterEnabled=True, lookupEnabled=True, defaultScope="okf_subdir")

    index = load_bundles([okf], config=cfg)

    assert index.pruned == 0
