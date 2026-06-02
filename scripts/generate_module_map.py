#!/usr/bin/env python3
"""Generate ARCHITECTURE.md from openmagi_core_agent source tree.

Parses all .py files via AST to extract module docstrings and import graphs,
then emits a Markdown document with a Mermaid dependency diagram and per-package
module tables.

Usage (from infra/docker/clawy-core-agent-python/):
    python3 scripts/generate_module_map.py > openmagi_core_agent/ARCHITECTURE.md
"""
from __future__ import annotations

import ast
import os
import sys
from collections import defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ROOT_PACKAGE = "openmagi_core_agent"
SKIP_IMPORTS = frozenset({"__future__"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_relative_import(
    module_file: Path,
    root_dir: Path,
    from_module: str | None,
    level: int,
) -> str | None:
    """Resolve a relative import to a dotted module path within the package."""
    if level == 0:
        return from_module

    # Walk up `level` directories from the file's parent
    base = module_file.parent
    for _ in range(level - 1):
        base = base.parent
        if base == root_dir.parent:
            return None  # went above the package

    try:
        rel = base.relative_to(root_dir)
    except ValueError:
        return None

    parts = list(rel.parts)
    dotted = ".".join([ROOT_PACKAGE] + parts)
    if from_module:
        dotted = f"{dotted}.{from_module}"
    return dotted


def _module_to_package(dotted: str) -> str | None:
    """Extract the top-level sub-package from a dotted module path.

    e.g. ``openmagi_core_agent.tools.dispatcher`` -> ``tools``
    """
    parts = dotted.split(".")
    if len(parts) < 2 or parts[0] != ROOT_PACKAGE:
        return None
    return parts[1]


def _parse_file(filepath: Path, root_dir: Path) -> tuple[str | None, list[str]]:
    """Parse a single .py file and return (docstring, list_of_import_targets).

    ``root_dir`` is the package root (e.g. ``openmagi_core_agent/``).
    Returns (None, []) for empty or unparseable files.
    """
    try:
        source = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, []

    if not source.strip():
        return None, []

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return None, []

    # Extract module docstring
    docstring: str | None = None
    if tree.body:
        first = tree.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            val = first.value
            if isinstance(val.value, str):
                docstring = val.value.strip().split("\n")[0]

    # Extract imports
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name and alias.name.split(".")[0] not in SKIP_IMPORTS:
                    imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            if module_name.split(".")[0] in SKIP_IMPORTS:
                continue

            level = node.level or 0
            if level > 0:
                resolved = _resolve_relative_import(
                    filepath, root_dir=root_dir,
                    from_module=module_name if module_name else None,
                    level=level,
                )
                if resolved:
                    imports.append(resolved)
            elif module_name:
                imports.append(module_name)

    return docstring, imports


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class ModuleInfo:
    __slots__ = (
        "rel_path", "filename", "package", "docstring", "imports",
        "internal_deps", "module_deps",
    )

    def __init__(self, rel_path: str, filename: str, package: str) -> None:
        self.rel_path = rel_path
        self.filename = filename
        self.package = package
        self.docstring: str | None = None
        self.imports: list[str] = []
        self.internal_deps: set[str] = set()  # cross-package deps (for Mermaid)
        self.module_deps: set[str] = set()  # all internal module-level deps (for table)


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


def scan_package(root: Path) -> list[ModuleInfo]:
    """Walk ``root`` and collect module info for every .py file."""
    modules: list[ModuleInfo] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            filepath = Path(dirpath) / fn
            rel = filepath.relative_to(root)
            parts = list(rel.parts)
            if len(parts) == 1:
                pkg = "(root)"
            else:
                pkg = "/".join(parts[:-1])

            info = ModuleInfo(rel_path=str(rel), filename=fn, package=pkg)
            docstring, imports = _parse_file(filepath, root)
            info.docstring = docstring
            info.imports = imports

            # Determine internal package dependencies
            for imp in imports:
                dep_pkg = _module_to_package(imp)
                if dep_pkg:
                    # Module-level dep: extract the last component as module name
                    imp_parts = imp.split(".")
                    dep_module = imp_parts[-1] if len(imp_parts) > 2 else dep_pkg
                    info.module_deps.add(dep_module)
                    # Cross-package dep (for Mermaid)
                    if dep_pkg != pkg.split("/")[0]:
                        info.internal_deps.add(dep_pkg.split("/")[0])

            modules.append(info)
    return modules


# ---------------------------------------------------------------------------
# Dependency graph (package-level)
# ---------------------------------------------------------------------------


def build_package_graph(modules: list[ModuleInfo]) -> dict[str, set[str]]:
    """Build a package -> set[dependency_packages] graph."""
    graph: dict[str, set[str]] = defaultdict(set)
    for m in modules:
        src_pkg = m.package.split("/")[0]
        if src_pkg == "(root)":
            continue
        # Ensure package appears even with no deps
        if src_pkg not in graph:
            graph[src_pkg] = set()
        for dep in m.internal_deps:
            if dep != src_pkg:
                graph[src_pkg].add(dep)
    return dict(graph)


def render_mermaid(graph: dict[str, set[str]]) -> str:
    """Render a Mermaid graph definition."""
    lines = ["```mermaid", "graph LR"]
    edges: set[tuple[str, str]] = set()
    for src, deps in sorted(graph.items()):
        for dep in sorted(deps):
            edges.add((src, dep))

    # Emit nodes that have no edges at all
    all_nodes = set(graph.keys())
    for deps in graph.values():
        all_nodes.update(deps)
    connected = set()
    for s, d in edges:
        connected.add(s)
        connected.add(d)
    for node in sorted(all_nodes - connected):
        lines.append(f"    {node}")

    for src, dep in sorted(edges):
        lines.append(f"    {src} --> {dep}")

    lines.append("```")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Reverse dependency index
# ---------------------------------------------------------------------------


def build_reverse_deps(modules: list[ModuleInfo]) -> dict[str, set[str]]:
    """Map module_filename -> set of modules (as package/filename) that depend on it."""
    # Build a mapping from dotted module path to (package, filename)
    path_to_id: dict[str, str] = {}
    for m in modules:
        # Build dotted path
        parts = m.rel_path.replace("/", ".").removesuffix(".py")
        dotted = f"{ROOT_PACKAGE}.{parts}"
        path_to_id[dotted] = f"{m.package}/{m.filename}"

    reverse: dict[str, set[str]] = defaultdict(set)
    for m in modules:
        src_id = f"{m.package}/{m.filename}"
        for imp in m.imports:
            # Check if import target is in our package
            target_id = path_to_id.get(imp)
            if not target_id:
                # Try matching init: e.g. openmagi_core_agent.tools -> openmagi_core_agent/tools/__init__.py
                init_try = imp + ".__init__"
                target_id = path_to_id.get(init_try)
            if target_id and target_id != src_id:
                reverse[target_id].add(src_id)

    return dict(reverse)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def render_markdown(modules: list[ModuleInfo], graph: dict[str, set[str]]) -> str:
    """Produce the full Markdown output."""
    reverse = build_reverse_deps(modules)
    lines: list[str] = []
    lines.append("# Module Purpose Map (auto-generated)")
    lines.append("")
    lines.append("## Dependency Graph")
    lines.append("")
    lines.append(render_mermaid(graph))
    lines.append("")
    lines.append("## Packages")

    # Group modules by package
    by_package: dict[str, list[ModuleInfo]] = defaultdict(list)
    for m in modules:
        by_package[m.package].append(m)

    for pkg in sorted(by_package.keys()):
        pkg_modules = by_package[pkg]
        display_pkg = pkg + "/" if pkg != "(root)" else "(root)"
        lines.append("")
        lines.append(f"### {display_pkg}")
        lines.append("")
        lines.append("| Module | Purpose | Depends On | Depended By |")
        lines.append("|---|---|---|---|")

        for m in sorted(pkg_modules, key=lambda x: x.filename):
            purpose = m.docstring if m.docstring else "\u2014"
            # Escape pipes in purpose
            purpose = purpose.replace("|", "\\|")

            # Depends on: internal module-level deps
            depends_on = ", ".join(sorted(m.module_deps)) if m.module_deps else "\u2014"

            # Depended by: find who imports this module
            mod_id = f"{m.package}/{m.filename}"
            depended_set = reverse.get(mod_id, set())
            if depended_set:
                # Show just package/filename for brevity
                depended_by = ", ".join(sorted(depended_set))
            else:
                depended_by = "\u2014"

            lines.append(f"| {m.filename} | {purpose} | {depends_on} | {depended_by} |")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point."""
    # Determine root directory: script expects to run from
    # infra/docker/clawy-core-agent-python/
    cwd = Path.cwd()
    root = cwd / ROOT_PACKAGE
    if not root.is_dir():
        print(
            f"ERROR: Cannot find {ROOT_PACKAGE}/ in {cwd}. "
            f"Run from infra/docker/clawy-core-agent-python/.",
            file=sys.stderr,
        )
        sys.exit(1)

    modules = scan_package(root)
    graph = build_package_graph(modules)
    md = render_markdown(modules, graph)
    print(md)


if __name__ == "__main__":
    main()
