"""PR1 (OKF zero-friction) — auto_type + frontmatter-optional (bundle_loader + config).

Phase 1 ONLY: when ``config.auto_type`` is ON, docs that would be skipped under
strict mode (no frontmatter / missing / non-string type) are indexed anyway with
a default ``type: "document"``. When OFF, behavior is byte-identical to today.

Every test is hermetic: bundles are built inside ``tmp_path`` and config is passed
explicitly (never ``os.environ``), so the suite is immune to shell ``MAGI_*``
pollution.
"""
from __future__ import annotations

from pathlib import Path

from magi_agent.knowledge.okf.bundle_loader import (
    _DEFAULT_DOC_TYPE,
    load_bundles,
)
from magi_agent.knowledge.okf.config import (
    MASTER_ENV_VAR,
    OkfConfig,
    resolve_okf_config,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _cfg(**overrides: object) -> OkfConfig:
    return OkfConfig(**overrides)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# auto_type OFF (default) — byte-identical guard
# ---------------------------------------------------------------------------


def test_default_doc_type_constant_is_document() -> None:
    assert _DEFAULT_DOC_TYPE == "document"


def test_off_no_frontmatter_skipped_and_counted(tmp_path: Path) -> None:
    """OFF (default): a no-frontmatter doc is skipped and counted — as today."""
    root = tmp_path / "bundle"
    _write(root / "plain.md", "# Plain\n\njust markdown, no frontmatter\n")
    index = load_bundles([root], config=_cfg())
    assert len(index.docs) == 0
    assert index.skipped_no_type == 1
    assert index.auto_typed == 0


def test_off_typed_doc_still_indexed(tmp_path: Path) -> None:
    """OFF: a doc with a valid ``type:`` is indexed exactly as today."""
    root = tmp_path / "bundle"
    _write(root / "ok.md", "---\ntype: note\ntitle: Good\n---\nbody\n")
    index = load_bundles([root], config=_cfg())
    assert len(index.docs) == 1
    assert index.docs[0].doc_type == "note"
    assert index.docs[0].title == "Good"
    assert index.skipped_no_type == 0
    assert index.auto_typed == 0


def test_off_missing_type_still_skipped(tmp_path: Path) -> None:
    """OFF: frontmatter present but no type → skipped + counted (byte-identical)."""
    root = tmp_path / "bundle"
    _write(root / "bad.md", "---\ntitle: NoType\n---\nbody\n")
    index = load_bundles([root], config=_cfg())
    assert len(index.docs) == 0
    assert index.skipped_no_type == 1
    assert index.auto_typed == 0


# ---------------------------------------------------------------------------
# auto_type ON — rescue paths
# ---------------------------------------------------------------------------


def test_on_no_frontmatter_indexed_as_document(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    full = "# Plain Heading\n\njust markdown, no frontmatter\n"
    _write(root / "plain.md", full)
    index = load_bundles([root], config=_cfg(autoType=True))
    assert len(index.docs) == 1
    doc = index.docs[0]
    assert doc.doc_type == _DEFAULT_DOC_TYPE == "document"
    # body is the FULL text (no frontmatter to strip).
    assert doc.body == full
    # title falls back to the H1.
    assert doc.title == "Plain Heading"
    assert index.skipped_no_type == 0
    assert index.auto_typed == 1


def test_on_no_frontmatter_title_falls_back_to_stem(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    _write(root / "readme.md", "just body text, no heading at all\n")
    index = load_bundles([root], config=_cfg(autoType=True))
    assert len(index.docs) == 1
    assert index.docs[0].title == "readme"
    assert index.docs[0].doc_type == "document"
    assert index.auto_typed == 1


def test_on_frontmatter_without_type_indexed_as_document(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    _write(root / "meta.md", "---\ntitle: Has Meta\ntags: [a, b]\n---\nbody\n")
    index = load_bundles([root], config=_cfg(autoType=True))
    assert len(index.docs) == 1
    doc = index.docs[0]
    assert doc.doc_type == "document"
    assert doc.title == "Has Meta"
    assert doc.tags == ("a", "b")
    assert "body" in doc.body
    assert index.auto_typed == 1


def test_on_non_string_type_int_indexed_as_document(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    _write(root / "n.md", "---\ntype: 123\ntitle: NumType\n---\nbody\n")
    index = load_bundles([root], config=_cfg(autoType=True))
    assert len(index.docs) == 1
    assert index.docs[0].doc_type == "document"
    assert index.auto_typed == 1


def test_on_non_string_type_list_indexed_as_document(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    _write(root / "l.md", "---\ntype: [x]\ntitle: ListType\n---\nbody\n")
    index = load_bundles([root], config=_cfg(autoType=True))
    assert len(index.docs) == 1
    assert index.docs[0].doc_type == "document"
    assert index.auto_typed == 1


def test_on_broken_non_dict_frontmatter_still_skipped(tmp_path: Path) -> None:
    """Broken YAML (non-Mapping) is NOT trusted even under auto_type."""
    root = tmp_path / "bundle"
    _write(root / "broken.md", "---\njust a string\n---\nbody\n")
    index = load_bundles([root], config=_cfg(autoType=True))
    assert len(index.docs) == 0
    assert index.skipped_no_type == 1
    assert index.auto_typed == 0


def test_on_valid_type_is_respected_not_overwritten(tmp_path: Path) -> None:
    """An explicit valid type is honored — auto_type never overwrites it."""
    root = tmp_path / "bundle"
    _write(root / "t.md", "---\ntype: table\ntitle: Real\n---\nbody\n")
    index = load_bundles([root], config=_cfg(autoType=True))
    assert len(index.docs) == 1
    assert index.docs[0].doc_type == "table"
    # explicit-type docs are NOT counted as auto-typed.
    assert index.auto_typed == 0


def test_on_mixed_bundle_counts_auto_typed_separately(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    _write(root / "typed.md", "---\ntype: table\n---\nbody\n")
    _write(root / "plain.md", "# Plain\n\nno frontmatter\n")
    _write(root / "notype.md", "---\ntitle: X\n---\nbody\n")
    index = load_bundles([root], config=_cfg(autoType=True))
    assert len(index.docs) == 3
    assert index.skipped_no_type == 0
    assert index.auto_typed == 2  # plain + notype rescued; typed not counted


# ---------------------------------------------------------------------------
# Config resolver — MAGI_KNOWLEDGE_OKF_AUTO_TYPE cascade
# ---------------------------------------------------------------------------


def test_auto_type_defaults_off_and_follows_master_off() -> None:
    cfg = resolve_okf_config(env={}, config={})
    assert cfg.auto_type is False


def test_auto_type_follows_master_on() -> None:
    # auto_type is a capability: master_default=master, so master-on turns it on.
    cfg = resolve_okf_config(env={MASTER_ENV_VAR: "1"}, config={})
    assert cfg.master_enabled is True
    assert cfg.auto_type is True


def test_auto_type_explicit_off_beats_master_on() -> None:
    cfg = resolve_okf_config(
        env={MASTER_ENV_VAR: "1", "MAGI_KNOWLEDGE_OKF_AUTO_TYPE": "0"},
        config={},
    )
    assert cfg.master_enabled is True
    assert cfg.auto_type is False


def test_auto_type_explicit_on_beats_master_off() -> None:
    cfg = resolve_okf_config(
        env={"MAGI_KNOWLEDGE_OKF_AUTO_TYPE": "1"},
        config={},
    )
    assert cfg.master_enabled is False
    assert cfg.auto_type is True


def test_auto_type_via_config_toml_table() -> None:
    cfg = resolve_okf_config(env={}, config={"knowledge_okf": {"auto_type": True}})
    assert cfg.auto_type is True
