"""C-2 meta-test: forbid re-forking the ``safe_metadata`` symbol.

After C-2, the only module-level ``def safe_metadata`` body that resolves the
strict / fail-closed semantics lives in ``magi_agent/ops/safety.py``. The web
acquisition copy at ``magi_agent/web_acquisition/policy.py:safe_metadata`` is a
one-line re-export shim (it just delegates to
``ops.safety.public_diagnostic_metadata``) and is explicitly allowlisted below.

Why this matters: before C-2, two ``safe_metadata`` symbols existed in the tree
with OPPOSITE fail-modes (strict allow-list vs. lenient deny-list) on a
redaction boundary. Same name, opposite guarantees. The lenient (weaker) form
was on the live web path. Any silent re-fork would reopen that silent-
weakening hazard, so this test fails on any second body-bearing definition.

AST-based — looks for top-level ``def safe_metadata`` (not strings, not
comments, not nested functions).
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "magi_agent"

# Canonical kernel home + documented re-export shim. The shim's body must be
# a thin delegation; the test still allows it, but the policy contract is that
# the shim contains ONLY a delegation call (see C-2 plan).
_KERNEL_FILE = "ops/safety.py"
_REEXPORT_SHIMS: frozenset[str] = frozenset(
    {
        "web_acquisition/policy.py",
    }
)


def _iter_module_files() -> list[Path]:
    return sorted(
        path
        for path in PACKAGE.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _files_defining_safe_metadata() -> list[str]:
    """Return relative paths of modules that define a *module-level*
    ``def safe_metadata`` (sync or async)."""
    hits: list[str] = []
    for path in _iter_module_files():
        rel = path.relative_to(PACKAGE).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if node.name == "safe_metadata":
                    hits.append(rel)
                    break
    return hits


def test_only_kernel_and_documented_shim_define_safe_metadata() -> None:
    """No module-level ``def safe_metadata`` may appear outside ``ops/safety.py``
    except the documented one-line re-export shim in ``web_acquisition/policy.py``.
    """
    offenders = sorted(
        rel
        for rel in _files_defining_safe_metadata()
        if rel != _KERNEL_FILE and rel not in _REEXPORT_SHIMS
    )
    assert not offenders, (
        "New forked `def safe_metadata` outside ops/safety.py (and the documented "
        "policy.py shim). Two safe_metadata definitions with opposite fail-modes "
        "is exactly the C-2 silent-weakening hazard the kernel fixes. Either:\n"
        "  (a) route the caller through magi_agent.ops.safety.safe_metadata "
        "      (strict allow-list) or "
        "magi_agent.ops.safety.public_diagnostic_metadata (lenient deny-list), or\n"
        "  (b) if a documented shim is needed, add the file to _REEXPORT_SHIMS "
        "      with a justification and keep the body a one-line delegation.\n"
        f"Offenders: {offenders}"
    )


def test_kernel_file_defines_safe_metadata() -> None:
    """The kernel home must keep the canonical definition (catches accidental
    deletion / rename of the strict symbol)."""
    assert _KERNEL_FILE in _files_defining_safe_metadata(), (
        f"{_KERNEL_FILE} must define a module-level `def safe_metadata`"
    )


def test_reexport_shim_is_thin_delegation() -> None:
    """The allowed shim in ``web_acquisition/policy.py`` must remain a thin
    delegation (≤ 25 statement nodes in its body). Catches accidental
    re-introduction of a divergent implementation."""
    shim_path = PACKAGE / "web_acquisition" / "policy.py"
    tree = ast.parse(shim_path.read_text(encoding="utf-8"), filename=str(shim_path))
    for node in tree.body:
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == "safe_metadata"
        ):
            statement_count = sum(1 for _ in ast.walk(node)) - 1  # exclude FunctionDef itself
            # A thin delegation is < 25 AST nodes (docstring + guard + one call).
            assert statement_count < 60, (
                f"web_acquisition/policy.py:safe_metadata grew to {statement_count} "
                "AST nodes — should be a thin delegation to "
                "ops.safety.public_diagnostic_metadata. Re-fork is C-2 hazard."
            )
            return
    raise AssertionError(
        "web_acquisition/policy.py must still define `safe_metadata` as the C-2 "
        "shim (one-line re-export)."
    )
