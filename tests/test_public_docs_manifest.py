from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
MANIFEST = DOCS / "manifest.json"
PRIVATE_DOC_PREFIXES = (
    "docs/notes/",
    "docs/plans/",
    "docs/superpowers/",
)
PLANNING_ONLY_MARKERS = (
    "Draft for review",
    "REQUIRED SUB-SKILL",
    "do NOT merge",
    "implementation session",
    "stacked PR retarget",
    "worktree:",
)


def _manifest_pages() -> list[dict[str, str]]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    pages = data.get("pages")
    assert isinstance(pages, list)
    return pages


def test_public_docs_manifest_paths_resolve_and_do_not_expose_internal_notes() -> None:
    for page in _manifest_pages():
        path = page["path"]
        assert not path.startswith(PRIVATE_DOC_PREFIXES), path
        assert (ROOT / path).is_file(), path


def test_public_docs_tree_has_no_internal_plan_or_memory_corpus() -> None:
    for prefix in PRIVATE_DOC_PREFIXES:
        directory = ROOT / prefix.rstrip("/")
        if not directory.exists():
            continue
        leaked_files = [path for path in directory.rglob("*") if path.is_file()]
        assert leaked_files == []


def test_public_docs_keep_recipe_and_harness_guides_visible() -> None:
    slugs = {page["slug"] for page in _manifest_pages()}
    assert {
        "recipes",
        "harnesses",
        "build-a-recipe",
        "build-a-harness",
        "source-verified-research",
        "coding-verification",
        "general-automation",
    } <= slugs


def test_machine_readable_docs_do_not_include_planning_only_markers() -> None:
    for path in (DOCS / "llms.txt", DOCS / "llms-full.txt"):
        text = path.read_text(encoding="utf-8")
        for marker in PLANNING_ONLY_MARKERS:
            assert marker not in text
