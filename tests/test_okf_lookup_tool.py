"""Tests for the redaction-free ``OkfLookup`` native tool (PR2).

Hermetic: every test passes env via ``monkeypatch.setenv`` and bundles via
``tmp_path``.  The tool is default-OFF (master switch ``MAGI_KNOWLEDGE_OKF_ENABLED``
resolves False) and, when enabled, returns ``OkfDoc`` fields VERBATIM — proving it
does NOT route through the fake-provider ``KnowledgeBoundary`` redaction scaffold.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from magi_agent.plugins.native.okf import okf_lookup
from magi_agent.tools.context import ToolContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OKF_ENV_VARS = (
    "MAGI_KNOWLEDGE_OKF_ENABLED",
    "MAGI_KNOWLEDGE_OKF_LOOKUP_ENABLED",
    "MAGI_KNOWLEDGE_OKF_INDEX_INJECT_ENABLED",
    "MAGI_OKF_BUNDLE_PATHS",
    "MAGI_KNOWLEDGE_OKF_MAX_RECORDS",
    "MAGI_KNOWLEDGE_OKF_MAX_PREVIEW_CHARS",
    "MAGI_KNOWLEDGE_OKF_MAX_DOCS",
    "MAGI_KNOWLEDGE_OKF_MAX_TOTAL_BYTES",
    "MAGI_AGENT_WORKSPACE",
)


def _clear_okf_env(monkeypatch) -> None:
    for name in _OKF_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _context(workspace_root: str | None = None) -> ToolContext:
    return ToolContext(
        bot_id="test-bot",
        session_id="session-a",
        session_key="session-a",
        workspace_root=workspace_root,
    )


def _write_doc(root: Path, rel: str, *, frontmatter: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}\n---\n{body}", encoding="utf-8")
    return path


def _enable(monkeypatch, bundle_root: Path) -> None:
    _clear_okf_env(monkeypatch)
    monkeypatch.setenv("MAGI_KNOWLEDGE_OKF_ENABLED", "1")
    monkeypatch.setenv("MAGI_KNOWLEDGE_OKF_LOOKUP_ENABLED", "1")
    monkeypatch.setenv("MAGI_OKF_BUNDLE_PATHS", str(bundle_root))


def _run(arguments: dict[str, object], context: ToolContext):
    return asyncio.run(okf_lookup(arguments, context))


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


def test_disabled_by_default_blocks(monkeypatch, tmp_path) -> None:
    _clear_okf_env(monkeypatch)
    # Even with a bundle path set, the master switch defaults OFF.
    monkeypatch.setenv("MAGI_OKF_BUNDLE_PATHS", str(tmp_path))

    result = _run({"query": "orders"}, _context())

    assert result.status == "blocked"
    assert result.error_code == "okf_disabled"


def test_master_on_but_lookup_off_blocks(monkeypatch, tmp_path) -> None:
    _clear_okf_env(monkeypatch)
    monkeypatch.setenv("MAGI_KNOWLEDGE_OKF_ENABLED", "1")
    monkeypatch.setenv("MAGI_KNOWLEDGE_OKF_LOOKUP_ENABLED", "0")
    monkeypatch.setenv("MAGI_OKF_BUNDLE_PATHS", str(tmp_path))

    result = _run({"query": "orders"}, _context())

    assert result.status == "blocked"
    assert result.error_code == "okf_disabled"


# ---------------------------------------------------------------------------
# Query semantics
# ---------------------------------------------------------------------------


def test_query_required_when_no_query_and_no_path(monkeypatch, tmp_path) -> None:
    _enable(monkeypatch, tmp_path)

    result = _run({}, _context())

    assert result.status == "blocked"
    assert result.error_code == "query_required"


def test_enabled_query_returns_verbatim_records(monkeypatch, tmp_path) -> None:
    bundle = tmp_path / "okf"
    _write_doc(
        bundle,
        "sales/tables/orders.md",
        frontmatter=(
            "type: table\n"
            "title: Orders\n"
            "description: order line items\n"
            "resource: https://example.com/orders\n"
            "tags: [sales, orders]"
        ),
        body="# Orders\n\nColumn schema for the orders table with revenue figures.",
    )
    _enable(monkeypatch, bundle)

    result = _run({"query": "orders"}, _context())

    assert result.status == "ok"
    output = result.output
    assert output["count"] == 1
    record = output["records"][0]
    # VERBATIM path with a slash proves no sha source_ref substitution.
    assert record["path"] == "sales/tables/orders.md"
    assert "/" in record["path"]
    assert record["title"] == "Orders"
    # Real body text in the preview (not a redacted public-safe snippet).
    assert "revenue figures" in record["preview"]
    assert record["resource"] == "https://example.com/orders"
    assert record["tags"] == ["sales", "orders"]
    assert record["type"] == "table"
    assert record["frontmatter"]["type"] == "table"


def test_direct_path_lookup_returns_exact_doc(monkeypatch, tmp_path) -> None:
    bundle = tmp_path / "okf"
    _write_doc(
        bundle,
        "sales/metrics/wau.md",
        frontmatter="type: metric\ntitle: Weekly Active Users",
        body="# WAU\n\nWeekly active users definition.",
    )
    # A second doc the query path would also have matched, to prove path is exact.
    _write_doc(
        bundle,
        "sales/metrics/dau.md",
        frontmatter="type: metric\ntitle: Daily Active Users",
        body="# DAU\n\nDaily active users definition.",
    )
    _enable(monkeypatch, bundle)

    result = _run({"path": "sales/metrics/wau.md"}, _context())

    assert result.status == "ok"
    assert result.output["count"] == 1
    record = result.output["records"][0]
    assert record["path"] == "sales/metrics/wau.md"
    assert record["title"] == "Weekly Active Users"
    assert "Weekly active users definition" in record["preview"]


def test_preview_respects_max_preview_chars(monkeypatch, tmp_path) -> None:
    bundle = tmp_path / "okf"
    long_body = "X" * 5000
    _write_doc(
        bundle,
        "big.md",
        frontmatter="type: note\ntitle: Big\ntags: [big]",
        body=long_body,
    )
    _enable(monkeypatch, bundle)
    monkeypatch.setenv("MAGI_KNOWLEDGE_OKF_MAX_PREVIEW_CHARS", "100")

    result = _run({"query": "big"}, _context())

    assert result.status == "ok"
    record = result.output["records"][0]
    assert len(record["preview"]) == 100


def test_empty_bundle_returns_ok_with_no_records(monkeypatch, tmp_path) -> None:
    bundle = tmp_path / "okf"
    bundle.mkdir(parents=True, exist_ok=True)
    _enable(monkeypatch, bundle)

    result = _run({"query": "anything"}, _context())

    assert result.status == "ok"
    assert result.output["records"] == []
    assert result.output["count"] == 0


def test_no_bundles_configured_is_ok_with_note_not_error(monkeypatch, tmp_path) -> None:
    # Enabled, but no bundle paths and no workspace knowledge/okf dir → not an error.
    _clear_okf_env(monkeypatch)
    monkeypatch.setenv("MAGI_KNOWLEDGE_OKF_ENABLED", "1")
    monkeypatch.setenv("MAGI_KNOWLEDGE_OKF_LOOKUP_ENABLED", "1")

    result = _run({"query": "orders"}, _context(workspace_root=str(tmp_path)))

    assert result.status == "ok"
    assert result.output["records"] == []
    assert result.output["count"] == 0
    assert "note" in result.output


def test_workspace_fallback_discovers_knowledge_okf(monkeypatch, tmp_path) -> None:
    # No MAGI_OKF_BUNDLE_PATHS — fall back to <workspace_root>/knowledge/okf.
    bundle = tmp_path / "knowledge" / "okf"
    _write_doc(
        bundle,
        "guide.md",
        frontmatter="type: guide\ntitle: Onboarding Guide",
        body="# Guide\n\nHow to onboard a new teammate.",
    )
    _clear_okf_env(monkeypatch)
    monkeypatch.setenv("MAGI_KNOWLEDGE_OKF_ENABLED", "1")
    monkeypatch.setenv("MAGI_KNOWLEDGE_OKF_LOOKUP_ENABLED", "1")

    result = _run({"query": "onboard"}, _context(workspace_root=str(tmp_path)))

    assert result.status == "ok"
    assert result.output["count"] == 1
    assert result.output["records"][0]["path"] == "guide.md"


# ---------------------------------------------------------------------------
# Catalog registration
# ---------------------------------------------------------------------------


def test_okf_lookup_registered_in_native_catalog() -> None:
    from magi_agent.plugins.native_catalog import native_plugin_by_id

    manifest = native_plugin_by_id("openmagi.knowledge-okf")
    assert manifest is not None
    tool_names = {tool.name for tool in manifest.tools}
    assert "OkfLookup" in tool_names
    assert "okf-lookup" in tool_names
    assert manifest.permissions == ("read",)
    # Catalog-level metadata advertises the surface; the live default-OFF gate is
    # the MAGI_KNOWLEDGE_OKF_* env switch enforced inside the tool itself.
    for tool in manifest.tools:
        assert tool.entrypoint == "magi_agent.plugins.native.okf:okf_lookup"
