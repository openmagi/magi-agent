"""Grandfathered layering ratchet (rem2/F2, deep-review N-07).

Freezes the current cross-package import graph so that SCC membership and
cross-package top-level edges may only SHRINK. Growth (a new back-edge or a
new cli back-import) fails the build; a shrink asks you to regenerate the
baseline to lock in the win.

Regenerate the baseline:
    python3 -m tests.layering.import_graph_scan > tests/layering/import_graph_baseline.json
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from tests.layering.import_graph_scan import (
    _toplevel_imports,
    compute_snapshot,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BASELINE_PATH = _REPO_ROOT / "tests" / "layering" / "import_graph_baseline.json"

_REGEN = (
    "python3 -m tests.layering.import_graph_scan > "
    "tests/layering/import_graph_baseline.json"
)


def _load_baseline() -> dict:
    assert _BASELINE_PATH.exists(), (
        "baseline missing; run: " + _REGEN
    )
    return json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))


def _computed() -> dict:
    return compute_snapshot(_REPO_ROOT)


def test_package_edges_match_baseline() -> None:
    baseline = set(_load_baseline()["package_edges"])
    computed = set(_computed()["package_edges"])
    new_edges = sorted(computed - baseline)
    stale_edges = sorted(baseline - computed)
    assert not new_edges, (
        "FORBIDDEN: new cross-package top-level import edge(s); invert or "
        "lazy-import, or justify in review and regenerate the baseline. "
        f"new_edges={new_edges}"
    )
    assert not stale_edges, (
        "SHRUNK: regenerate the baseline to lock in the win: "
        + _REGEN
        + f" stale_edges={stale_edges}"
    )


def test_multi_package_scc_membership_matches_baseline() -> None:
    baseline_members = {m for scc in _load_baseline()["multi_package_sccs"] for m in scc}
    computed_members = {m for scc in _computed()["multi_package_sccs"] for m in scc}
    new_members = sorted(computed_members - baseline_members)
    left_members = sorted(baseline_members - computed_members)
    assert not new_members, (
        "FORBIDDEN: package(s) newly pulled into a multi-package cycle; "
        "break the cycle instead. "
        f"new_members={new_members}"
    )
    assert not left_members, (
        "SHRUNK: package(s) left a cycle; regenerate the baseline to lock in "
        "the win: "
        + _REGEN
        + f" left_members={left_members}"
    )


def test_cli_back_import_site_inventory_matches_baseline() -> None:
    baseline = set(_load_baseline()["cli_back_import_sites"])
    computed = set(_computed()["cli_back_import_sites"])
    new_sites = sorted(computed - baseline)
    stale_sites = sorted(baseline - computed)
    assert not new_sites, (
        "FORBIDDEN: new non-cli import of magi_agent.cli.*; import from "
        "magi_agent.engine.* instead. "
        f"new_sites={new_sites}"
    )
    assert not stale_sites, (
        "SHRUNK: regenerate the baseline to lock in the win: "
        + _REGEN
        + f" stale_sites={stale_sites}"
    )


# ---------------------------------------------------------------------------
# Golden mini test: the top-level classifier must include module-body and
# module-level if/try imports, exclude TYPE_CHECKING blocks and function
# bodies (lazy imports).
# ---------------------------------------------------------------------------


def test_scanner_classifies_type_checking_and_lazy() -> None:
    source = (
        "from __future__ import annotations\n"
        "from typing import TYPE_CHECKING\n"
        "import pkg_toplevel\n"
        "from pkg_from import thing\n"
        "if TYPE_CHECKING:\n"
        "    from pkg_typecheck import gated\n"
        "try:\n"
        "    import pkg_try\n"
        "except ImportError:\n"
        "    pkg_try = None\n"
        "if True:\n"
        "    from pkg_if import cond\n"
        "def loader():\n"
        "    from pkg_lazy import deferred\n"
        "    return deferred\n"
    )
    tree = ast.parse(source)
    found = set(_toplevel_imports(tree))
    assert "pkg_toplevel" in found
    assert "pkg_from" in found
    assert "pkg_try" in found
    assert "pkg_if" in found
    assert "pkg_typecheck" not in found
    assert "pkg_lazy" not in found


def test_snapshot_is_deterministic() -> None:
    first = compute_snapshot(_REPO_ROOT)
    second = compute_snapshot(_REPO_ROOT)
    assert first == second
