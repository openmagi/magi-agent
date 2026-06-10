"""Guards the Stage 1/2/3 governance doc that defines default-off-gate promotion
(PR 15-PR1).

Two factual doc gaps motivate these checks:

A9 / E1 — ``internal/docs/developer-overview.md`` linked
``/docs/default-off-gates`` as the "staged rollout process," but the page did
not exist (dead link, marked "planned, not yet published") and Stage 1/2 were
never defined anywhere (only "Stage 3" was named in ``what-works-today.md``).

This PR is pure documentation: it adds ``docs/default-off-gates.md`` defining
Stage 1/2/3 and the promotion/deletion policy, fixes the dead link, and wires
the page into the docs manifest. These are file-content assertions only — no
network, no runtime.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATES = ROOT / "docs" / "default-off-gates.md"
DEV_OVERVIEW = ROOT / "internal" / "docs" / "developer-overview.md"
WHAT_WORKS = ROOT / "docs" / "what-works-today.md"
MANIFEST = ROOT / "docs" / "manifest.json"


def test_default_off_gates_page_exists() -> None:
    assert GATES.is_file(), "docs/default-off-gates.md must exist (was a dead link)"


def test_default_off_gates_defines_all_three_stages() -> None:
    text = GATES.read_text(encoding="utf-8")
    # Each stage must have its own section header so the promotion ladder is
    # navigable and the developer-overview link lands on real content.
    assert "## Stage 1" in text
    assert "## Stage 2" in text
    assert "## Stage 3" in text


def test_default_off_gates_states_promotion_and_deletion_policy() -> None:
    text = GATES.read_text(encoding="utf-8").lower()
    # The "seam = enable-able or deleted within N releases" policy must be
    # spelled out, not implied.
    assert "promot" in text
    assert "delet" in text
    assert "release" in text


def test_default_off_gates_maps_representative_flags_to_stages() -> None:
    text = GATES.read_text(encoding="utf-8")
    # Anchor the abstract stages to concrete, verifiably-existing flags so the
    # ladder is actionable. All of these are registered in
    # magi_agent/config/flags.py / read in config/env.py on origin/main.
    for flag in (
        "MAGI_MEMORY_ENABLED",
        "MAGI_CONTROL_STAGE",
        "MAGI_DEEP_WEB_RESEARCH_ENABLED",
        "MAGI_NATIVE_RECEIPTS_HONEST",
    ):
        assert flag in text, flag


def test_developer_overview_link_no_longer_marked_planned() -> None:
    text = DEV_OVERVIEW.read_text(encoding="utf-8")
    # The page now exists, so the link must not be tagged "planned, not yet
    # published" — that wording sends contributors away from real content.
    assert "/docs/default-off-gates" in text
    assert "page planned, not yet published" not in text


def test_what_works_today_links_to_gates_for_stage_definitions() -> None:
    text = WHAT_WORKS.read_text(encoding="utf-8")
    # The "Stage 3" mention should point readers to where Stage 1/2/3 are
    # actually defined.
    assert "/docs/default-off-gates" in text


def test_manifest_exposes_default_off_gates_page() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    paths = {page["path"] for page in manifest["pages"]}
    assert "docs/default-off-gates.md" in paths
