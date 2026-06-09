from __future__ import annotations

import fnmatch
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
MANIFEST = DOCS / "manifest.json"
PACKAGED_DASHBOARD_LLMS = ROOT / "magi_agent" / "web_dashboard" / "llms.txt"
BANNED_PUBLIC_DOC_PREFIXES = (
    "docs/architecture/parity/",
    "docs/internal/",
    "docs/notes/",
    "docs/plans/",
    "docs/superpowers/",
)
INTERNAL_PLANNING_DOC_NAME_PATTERNS = (
    "*-handoff*.md",
    "*-plan.md",
)
INTERNAL_PLANNING_DOC_NAME_ALLOWLIST: frozenset[str] = frozenset()
PLANNING_ONLY_MARKERS = (
    "Draft for review",
    "REQUIRED SUB-SKILL",
    "do NOT merge",
    "implementation session",
    "stacked PR retarget",
    "worktree:",
    "worktree at",
    "magi-agent-oss-worktrees",
    "Track 19",
    "PR #",
)
STALE_RUNTIME_MARKERS = (
    "ADK invocation is scaffolded but disabled",
    "ADK invocation DISABLED",
    "ADK Runner (currently DISABLED)",
    "All tool dispatch is currently BLOCKED",
    "Dispatcher is BLOCKED",
    "currently BLOCKED",
    "toolDispatchAllowed=False",
    "HarnessRule (TypeScript",
    "RuntimePolicy interface (TypeScript",
    "TypeScript and Python interfaces exposed",
    "policyTypes.ts",
    "npm run magi",
)
STALE_TYPESCRIPT_OR_NODE_MARKERS = (
    "TypeScript strict mode",
    "No `any` type",
    "npm install",
    "npm run dev",
    "npm test",
    "npm run lint",
    "Node.js version",
)
INTERNAL_HOSTED_MARKERS = (
    "hosted runtime",
    "hosted deployment",
    "hosted-runtime",
    "managed deployment",
    "selected-bot rollout",
)


def _manifest_pages() -> list[dict[str, str]]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    pages = data.get("pages")
    assert isinstance(pages, list)
    return pages


def _is_banned_public_doc_path(path: str) -> bool:
    if path.startswith(BANNED_PUBLIC_DOC_PREFIXES):
        return True
    if (
        path not in INTERNAL_PLANNING_DOC_NAME_ALLOWLIST
        and path.startswith("docs/")
        and path.endswith(".md")
    ):
        filename = Path(path).name
        return any(
            fnmatch.fnmatch(filename, pattern)
            for pattern in INTERNAL_PLANNING_DOC_NAME_PATTERNS
        )
    return False


def test_public_docs_manifest_paths_resolve_and_do_not_expose_banned_internal_docs() -> None:
    for page in _manifest_pages():
        path = page["path"]
        assert not _is_banned_public_doc_path(path), path
        assert (ROOT / path).is_file(), path


def test_public_docs_manifest_paths_and_slugs_are_unique() -> None:
    pages = _manifest_pages()
    for key in ("path", "slug"):
        values = [page[key] for page in pages]
        assert len(values) == len(set(values)), key


def test_every_markdown_doc_is_manifest_linked_public_corpus() -> None:
    manifest_paths = {page["path"] for page in _manifest_pages()}
    markdown_paths = {
        path.relative_to(ROOT).as_posix()
        for path in DOCS.rglob("*.md")
    }
    assert markdown_paths <= manifest_paths


def test_public_docs_tree_has_no_banned_internal_docs_or_planning_names() -> None:
    leaked_files: list[str] = []
    for path in DOCS.rglob("*"):
        if not path.is_file():
            continue
        relative_path = path.relative_to(ROOT).as_posix()
        if _is_banned_public_doc_path(relative_path):
            leaked_files.append(relative_path)

    assert leaked_files == []


def test_public_docs_tree_has_no_internal_planning_directories() -> None:
    banned_directories = (
        DOCS / "plans",
        DOCS / "superpowers",
    )
    existing_directories = [
        path.relative_to(ROOT).as_posix()
        for path in banned_directories
        if path.exists()
    ]
    assert existing_directories == []


@pytest.mark.parametrize(
    "path",
    (
        "docs/plans/2026-06-06-magi-cli-slash-commands-spec.md",
        "docs/notes/runtime-decision.md",
        "docs/superpowers/specs/guard-design.md",
        "docs/internal/release-checklist.md",
        "docs/architecture/parity/runtime-parity.md",
    ),
)
def test_internal_public_doc_directories_are_explicitly_banned(path: str) -> None:
    assert _is_banned_public_doc_path(path)


@pytest.mark.parametrize(
    "path",
    (
        "docs/magi-cli-plan.md",
        "docs/architecture/runtime-plan.md",
        "docs/release-handoff.md",
        "docs/architecture/runtime-handoff-2026.md",
    ),
)
def test_internal_planning_doc_names_are_explicitly_banned(path: str) -> None:
    assert _is_banned_public_doc_path(path)


def test_public_docs_keep_recipe_and_harness_guides_visible() -> None:
    slugs = {page["slug"] for page in _manifest_pages()}
    assert {
        "recipes",
        "harnesses",
        "first-party-packs",
        "build-a-recipe",
        "build-a-harness",
        "source-verified-research",
        "coding-verification",
        "general-automation",
        "streaming-events",
    } <= slugs


def test_docs_landing_pages_link_to_openmagi_site_and_source() -> None:
    for path in (ROOT / "README.md", DOCS / "README.md"):
        text = path.read_text(encoding="utf-8")
        assert "https://openmagi.ai" in text, path.relative_to(ROOT)
        assert "https://github.com/openmagi/magi-agent" in text, path.relative_to(ROOT)


def test_machine_readable_docs_do_not_include_planning_only_markers() -> None:
    for path in (DOCS / "llms.txt", DOCS / "llms-full.txt", PACKAGED_DASHBOARD_LLMS):
        text = path.read_text(encoding="utf-8")
        for marker in PLANNING_ONLY_MARKERS:
            assert marker not in text


def test_public_docs_do_not_describe_stale_disabled_or_typescript_runtime_state() -> None:
    public_docs = [ROOT / page["path"] for page in _manifest_pages()]
    public_docs.extend((DOCS / "llms.txt", DOCS / "llms-full.txt", PACKAGED_DASHBOARD_LLMS))

    for path in public_docs:
        text = path.read_text(encoding="utf-8")
        for marker in STALE_RUNTIME_MARKERS:
            assert marker not in text, f"{marker!r} leaked into {path.relative_to(ROOT)}"


def test_public_docs_do_not_include_internal_worktree_pr_or_track_residue() -> None:
    public_docs = [ROOT / page["path"] for page in _manifest_pages()]
    public_docs.extend(
        (ROOT / "README.md", DOCS / "llms.txt", DOCS / "llms-full.txt", PACKAGED_DASHBOARD_LLMS)
    )

    for path in public_docs:
        text = path.read_text(encoding="utf-8")
        for marker in PLANNING_ONLY_MARKERS:
            assert marker not in text, f"{marker!r} leaked into {path.relative_to(ROOT)}"


def test_public_docs_do_not_include_stale_node_or_typescript_contributor_residue() -> None:
    public_docs = [ROOT / page["path"] for page in _manifest_pages()]
    public_docs.extend(
        (ROOT / "README.md", DOCS / "llms.txt", DOCS / "llms-full.txt", PACKAGED_DASHBOARD_LLMS)
    )

    for path in public_docs:
        text = path.read_text(encoding="utf-8")
        for marker in STALE_TYPESCRIPT_OR_NODE_MARKERS:
            assert marker not in text, f"{marker!r} leaked into {path.relative_to(ROOT)}"


def test_public_docs_do_not_include_internal_hosted_rollout_residue() -> None:
    public_docs = [ROOT / page["path"] for page in _manifest_pages()]
    public_docs.extend(
        (ROOT / "README.md", DOCS / "llms.txt", DOCS / "llms-full.txt", PACKAGED_DASHBOARD_LLMS)
    )

    for path in public_docs:
        text = path.read_text(encoding="utf-8").lower()
        for marker in INTERNAL_HOSTED_MARKERS:
            assert marker not in text, f"{marker!r} leaked into {path.relative_to(ROOT)}"
