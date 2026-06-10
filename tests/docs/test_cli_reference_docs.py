"""Guards the CLI-surface docs against drift from the code (PR 17-PR1).

Three factual doc gaps motivated these checks:
- The `magi gateway` command family (status/start/install/uninstall) had zero
  docs even though it is a real, user-visible Typer sub-app
  (`magi_agent/cli/app.py`).
- `docs/cli.md` and `docs/cli/magi.md` listed only three permission modes while
  the code exposes four (`smartApprove` was missing).
- The `--mode plan|act` agent-mode flag (`magi_agent/cli/app.py`) was
  undocumented in every CLI page.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
MANIFEST = DOCS / "manifest.json"


def _manifest_pages() -> list[dict[str, str]]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    pages = data["pages"]
    assert isinstance(pages, list)
    return pages


def test_gateway_page_is_registered_in_manifest_and_exists() -> None:
    pages = _manifest_pages()
    gateway = [page for page in pages if page.get("slug") == "cli-gateway"]
    assert len(gateway) == 1, "expected exactly one cli-gateway manifest entry"
    page = gateway[0]
    assert page["path"] == "docs/cli/gateway.md"
    assert (ROOT / page["path"]).is_file()


def test_gateway_doc_covers_every_subcommand() -> None:
    text = (DOCS / "cli" / "gateway.md").read_text(encoding="utf-8")
    for subcommand in ("status", "start", "install", "uninstall"):
        assert f"magi gateway {subcommand}" in text, subcommand
    # The daemon gate must be named so the doc matches the code default-OFF.
    assert "MAGI_GATEWAY_DAEMON_ENABLED" in text


def test_cli_md_documents_four_permission_modes() -> None:
    text = (DOCS / "cli.md").read_text(encoding="utf-8")
    for mode in ("default", "acceptEdits", "bypassPermissions", "smartApprove"):
        assert mode in text, mode
    assert "three permission modes" not in text


def test_cli_magi_reference_documents_four_permission_modes() -> None:
    text = (DOCS / "cli" / "magi.md").read_text(encoding="utf-8")
    assert "smartApprove" in text
    # The Options block must list the fourth mode too, not just the prose table.
    assert "default|acceptEdits|bypassPermissions|smartApprove" in text


def test_cli_docs_document_mode_plan_act_flag() -> None:
    for relative in ("cli.md", "cli/magi.md"):
        text = (DOCS / relative).read_text(encoding="utf-8")
        assert "--mode" in text, relative
        assert "plan" in text and "act" in text, relative
