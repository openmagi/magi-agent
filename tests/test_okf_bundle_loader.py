"""PR1 — OKF bundle loader: parse, safety, caps, cache (all hermetic).

Sample OKF bundles are built inside ``tmp_path`` per-test so the suite is
self-contained and never reads a checked-in fixture or the real workspace.
"""
from __future__ import annotations

import os
from pathlib import Path

from magi_agent.knowledge.okf.bundle_loader import (
    OkfBundleIndex,
    OkfDoc,
    load_bundles,
)
from magi_agent.knowledge.okf.config import OkfConfig


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _cfg(**overrides: object) -> OkfConfig:
    return OkfConfig(**overrides)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Happy path + field mapping
# ---------------------------------------------------------------------------


def test_happy_path_parses_frontmatter_and_body(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    _write(
        root / "sales" / "tables" / "orders.md",
        "---\n"
        "type: table\n"
        "title: Orders\n"
        "description: order records\n"
        "resource: https://example.com/orders\n"
        "tags: [sales, orders]\n"
        "---\n"
        "# Orders\n\nSchema table body.\n",
    )
    index = load_bundles([root], config=_cfg())
    assert isinstance(index, OkfBundleIndex)
    assert len(index.docs) == 1
    doc = index.docs[0]
    assert isinstance(doc, OkfDoc)
    assert doc.rel_path == "sales/tables/orders.md"
    assert doc.bundle_root == str(root)
    assert doc.doc_type == "table"
    assert doc.title == "Orders"
    assert doc.description == "order records"
    assert doc.resource == "https://example.com/orders"
    assert doc.tags == ("sales", "orders")
    assert "Schema table body." in doc.body
    assert doc.frontmatter["type"] == "table"
    assert doc.byte_size > 0
    assert len(doc.content_digest) == 64  # sha256 hex
    assert doc.truncated is False


def test_missing_type_skipped_and_counted(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    _write(root / "ok.md", "---\ntype: note\ntitle: Good\n---\nbody\n")
    _write(root / "bad.md", "---\ntitle: NoType\n---\nbody\n")
    _write(root / "plain.md", "no frontmatter here\n")
    index = load_bundles([root], config=_cfg())
    assert len(index.docs) == 1
    assert index.docs[0].title == "Good"
    # both the type-less frontmatter doc and the no-frontmatter doc are counted.
    assert index.skipped_no_type == 2


def test_inline_array_tags_normalized(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    _write(root / "a.md", "---\ntype: t\ntags: [a, b, c]\n---\nx\n")
    index = load_bundles([root], config=_cfg())
    assert index.docs[0].tags == ("a", "b", "c")


def test_csv_string_tags_normalized(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    _write(root / "a.md", "---\ntype: t\ntags: a, b, c\n---\nx\n")
    index = load_bundles([root], config=_cfg())
    assert index.docs[0].tags == ("a", "b", "c")


def test_nested_yaml_frontmatter_parses(tmp_path: Path) -> None:
    # Proves yaml.safe_load (not a line parser): nested mapping + list survive.
    root = tmp_path / "bundle"
    _write(
        root / "a.md",
        "---\n"
        "type: dataset\n"
        "schema:\n"
        "  columns:\n"
        "    - name: id\n"
        "      kind: int\n"
        "    - name: email\n"
        "      kind: string\n"
        "---\n"
        "body\n",
    )
    index = load_bundles([root], config=_cfg())
    fm = index.docs[0].frontmatter
    assert fm["schema"] == {
        "columns": [
            {"name": "id", "kind": "int"},
            {"name": "email", "kind": "string"},
        ]
    }


def test_title_fallback_h1_then_stem(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    _write(root / "with_h1.md", "---\ntype: t\n---\n# Heading Title\n\nbody\n")
    _write(root / "no_h1.md", "---\ntype: t\n---\njust body, no heading\n")
    index = load_bundles([root], config=_cfg())
    by_rel = {d.rel_path: d for d in index.docs}
    assert by_rel["with_h1.md"].title == "Heading Title"
    assert by_rel["no_h1.md"].title == "no_h1"


# ---------------------------------------------------------------------------
# Safety: path traversal + secret basenames
# ---------------------------------------------------------------------------


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    _write(outside / "secret_doc.md", "---\ntype: t\ntitle: Leaked\n---\nsecret\n")
    root = tmp_path / "bundle"
    root.mkdir(parents=True, exist_ok=True)
    _write(root / "real.md", "---\ntype: t\ntitle: Real\n---\nok\n")
    # A symlink inside the bundle pointing at an out-of-tree file must not load.
    link = root / "escape.md"
    os.symlink(outside / "secret_doc.md", link)
    index = load_bundles([root], config=_cfg())
    titles = {d.title for d in index.docs}
    assert "Leaked" not in titles
    assert "Real" in titles
    assert index.skipped_unsafe >= 1


def test_secret_basenames_skipped(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    _write(root / "ok.md", "---\ntype: t\ntitle: Ok\n---\nbody\n")
    # ``.env`` is not a *.md file, but api_key.md and a credential file are.
    _write(root / "api_key.md", "---\ntype: t\ntitle: Key\n---\nsk-xxxx\n")
    _write(root / "my_secret.md", "---\ntype: t\ntitle: Secret\n---\nshh\n")
    index = load_bundles([root], config=_cfg())
    titles = {d.title for d in index.docs}
    assert titles == {"Ok"}
    assert index.skipped_unsafe >= 2


def test_dotenv_md_basename_skipped(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    _write(root / ".env.md", "---\ntype: t\ntitle: Env\n---\nKEY=v\n")
    _write(root / "fine.md", "---\ntype: t\ntitle: Fine\n---\nok\n")
    index = load_bundles([root], config=_cfg())
    assert {d.title for d in index.docs} == {"Fine"}


# ---------------------------------------------------------------------------
# Caps
# ---------------------------------------------------------------------------


def test_per_doc_byte_cap_truncates_body(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    big_body = "x" * 5000
    _write(root / "big.md", f"---\ntype: t\ntitle: Big\n---\n{big_body}\n")
    cfg = OkfConfig(maxDocBytes=1000)
    index = load_bundles([root], config=cfg)
    doc = index.docs[0]
    assert doc.truncated is True
    assert len(doc.body.encode("utf-8")) <= 1000


def test_global_max_docs_drop_counting(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    for i in range(5):
        _write(root / f"d{i}.md", f"---\ntype: t\ntitle: D{i}\n---\nbody {i}\n")
    cfg = OkfConfig(maxDocs=3)
    index = load_bundles([root], config=cfg)
    assert len(index.docs) == 3
    assert index.dropped_capped == 2


def test_global_max_total_bytes_drop_counting(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    body = "y" * 900
    for i in range(5):
        _write(root / f"d{i}.md", f"---\ntype: t\ntitle: D{i}\n---\n{body}\n")
    cfg = OkfConfig(maxTotalBytes=2000)
    index = load_bundles([root], config=cfg)
    # Each doc is ~1KB; only a couple fit under 2000 bytes.
    assert len(index.docs) < 5
    assert index.dropped_capped >= 1


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_cache_hit_when_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    _write(root / "a.md", "---\ntype: t\ntitle: A\n---\nbody\n")
    cfg = _cfg()
    first = load_bundles([root], config=cfg)
    second = load_bundles([root], config=cfg)
    # Same underlying doc object reused from cache (unchanged mtime/size).
    assert first.docs[0] is second.docs[0]


def test_cache_reloads_on_change(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    path = root / "a.md"
    _write(path, "---\ntype: t\ntitle: First\n---\nbody\n")
    cfg = _cfg()
    first = load_bundles([root], config=cfg)
    assert first.docs[0].title == "First"
    # Rewrite with a newer mtime + different content.
    _write(path, "---\ntype: t\ntitle: Second\n---\nchanged body\n")
    os.utime(path, (path.stat().st_atime + 10, path.stat().st_mtime + 10))
    second = load_bundles([root], config=cfg)
    assert second.docs[0].title == "Second"
    assert first.docs[0] is not second.docs[0]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def test_search_returns_matching_docs(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    _write(root / "orders.md", "---\ntype: t\ntitle: Orders\ntags: [sales]\n---\norder data\n")
    _write(root / "weather.md", "---\ntype: t\ntitle: Weather\n---\nrain and sun\n")
    index = load_bundles([root], config=_cfg())
    hits = index.search("orders", max_records=5)
    assert [d.title for d in hits] == ["Orders"]


def test_search_respects_max_records(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    for i in range(4):
        _write(root / f"d{i}.md", f"---\ntype: t\ntitle: shared{i}\n---\nshared body\n")
    index = load_bundles([root], config=_cfg())
    hits = index.search("shared", max_records=2)
    assert len(hits) == 2
