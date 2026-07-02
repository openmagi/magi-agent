"""Grandfathered layering ratchet scanner (rem2/F2, deep-review N-07).

Pure-stdlib AST scanner over ``magi_agent/`` that computes a deterministic
snapshot of the cross-package import graph:

* ``package_edges``: cross-package TOP-LEVEL import edges (module body plus
  module-level ``if``/``try`` blocks, excluding ``if TYPE_CHECKING:`` and
  function/class bodies).
* ``multi_package_sccs``: strongly connected components of size >= 2 in the
  package graph (Tarjan).
* ``cli_back_import_sites``: every reference (top-level OR lazy) from a
  non-cli module to ``magi_agent.cli`` / ``magi_agent.cli.*``.

The scanner NEVER imports magi_agent (import-based scanning would wobble with
optional-dep environments). Regenerate the baseline with:

    python3 -m tests.layering.import_graph_scan > tests/layering/import_graph_baseline.json

Known limitation: module-level class-body imports are not collected (rare).
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT_PACKAGE = "magi_agent"

_BASELINE_COMMENT = (
    "Grandfathered layering ratchet (rem2/F2, deep-review N-07). Regenerate: "
    "python3 -m tests.layering.import_graph_scan > "
    "tests/layering/import_graph_baseline.json. Edges/SCC membership may only "
    "SHRINK; any addition needs explicit review. N-24 (ops->gateway/harness "
    "lazy), N-25 (hooks<->harness top-level pair), N-27 (storage->missions/"
    "runtime top-level) are knowingly grandfathered per the 2026-07-02 deep "
    "review adversarial demotes."
)


def _toplevel_imports(tree: ast.Module) -> list[str]:
    """Return dotted module names imported at runtime import-time.

    Collects module body statements and module-level ``if``/``try`` blocks,
    but skips ``if TYPE_CHECKING:`` blocks and function/class bodies.
    """

    out: list[str] = []

    def visit_block(body) -> None:
        for node in body:
            if isinstance(node, ast.Import):
                out.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    out.append(node.module)
            elif isinstance(node, ast.If):
                t = node.test
                is_tc = (isinstance(t, ast.Name) and t.id == "TYPE_CHECKING") or (
                    isinstance(t, ast.Attribute) and t.attr == "TYPE_CHECKING"
                )
                if not is_tc:
                    visit_block(node.body)
                    visit_block(node.orelse)
            elif isinstance(node, ast.Try):
                visit_block(node.body)
                for h in node.handlers:
                    visit_block(h.body)
                visit_block(node.orelse)
                visit_block(node.finalbody)

    visit_block(tree.body)
    return out


def _all_module_targets(tree: ast.Module) -> list[str]:
    """Return every imported dotted module name (top-level AND lazy)."""

    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                out.append(node.module)
    return out


def _packages(pkg_root: Path) -> set[str]:
    """Direct child directories of magi_agent/ containing at least one .py."""

    packages: set[str] = set()
    for child in pkg_root.iterdir():
        if not child.is_dir() or child.name == "__pycache__":
            continue
        if any(child.rglob("*.py")):
            packages.add(child.name)
    return packages


def _dst_package(module: str, packages: set[str]) -> str | None:
    prefix = ROOT_PACKAGE + "."
    if not module.startswith(prefix):
        return None
    rest = module[len(prefix):]
    head = rest.split(".", 1)[0]
    if head in packages:
        return head
    return None


def _tarjan_scc(nodes: list[str], edges: dict[str, set[str]]) -> list[list[str]]:
    sys.setrecursionlimit(10000)
    index_counter = [0]
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    result: list[list[str]] = []

    def strongconnect(v: str) -> None:
        indices[v] = index_counter[0]
        lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in sorted(edges.get(v, ())):
            if w not in indices:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], indices[w])
        if lowlink[v] == indices[v]:
            component: list[str] = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                component.append(w)
                if w == v:
                    break
            result.append(sorted(component))

    for node in sorted(nodes):
        if node not in indices:
            strongconnect(node)
    return result


def compute_snapshot(repo_root: str | Path) -> dict:
    repo_root = Path(repo_root)
    pkg_root = repo_root / ROOT_PACKAGE
    packages = _packages(pkg_root)

    edges: dict[str, set[str]] = {p: set() for p in packages}
    cli_sites: set[str] = set()

    for path in sorted(pkg_root.rglob("*.py")):
        rel_parts = path.relative_to(pkg_root).parts
        src_pkg = rel_parts[0] if len(rel_parts) > 1 else None
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue

        if src_pkg is not None and src_pkg in packages:
            for module in _toplevel_imports(tree):
                dst = _dst_package(module, packages)
                if dst is not None and dst != src_pkg:
                    edges[src_pkg].add(dst)

        # cli back-import inventory: every non-cli file that references
        # magi_agent.cli or magi_agent.cli.* (top-level or lazy).
        if src_pkg != "cli":
            rel = path.relative_to(repo_root).as_posix()
            for module in _all_module_targets(tree):
                if module == "magi_agent.cli" or module.startswith(
                    "magi_agent.cli."
                ):
                    cli_sites.add(f"{rel}::{module}")

    package_edges = sorted(
        f"{src}->{dst}" for src, dsts in edges.items() for dst in dsts
    )
    sccs = _tarjan_scc(sorted(packages), edges)
    multi_sccs = sorted(
        (scc for scc in sccs if len(scc) >= 2), key=lambda s: s[0]
    )

    return {
        "_comment": _BASELINE_COMMENT,
        "package_count": len(packages),
        "package_edges": package_edges,
        "multi_package_sccs": multi_sccs,
        "cli_back_import_sites": sorted(cli_sites),
    }


def _repo_root() -> Path:
    # tests/layering/import_graph_scan.py -> repo root is parents[2].
    return Path(__file__).resolve().parents[2]


if __name__ == "__main__":
    snapshot = compute_snapshot(_repo_root())
    print(json.dumps(snapshot, indent=2, sort_keys=True))
