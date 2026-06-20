"""C-9 meta-test: forbid forked credential vocabularies.

After C-9 the credential-shaped-label vocabulary lives in
:mod:`magi_agent.security.credential_vocab`. Any re-fork would reopen the
silent-divergence hazard C-9 closes: three lists (lease side, SSRF side,
implicit issuer side) that "look like" each other but reject different
shapes.

AST-based, looking for:

* A top-level identifier ``_LEASE_RE`` / ``LEASE_REF_RE`` assigned to a
  ``re.compile`` of a literal containing the substring ``credential-lease:``
  outside the canonical vocab module.
* A top-level identifier ``_CREDENTIAL_QUERY_KEYS`` / ``CREDENTIAL_QUERY_KEYS``
  / ``_SENSITIVE_LEASE_FRAGMENTS`` / ``SENSITIVE_LEASE_FRAGMENTS`` assigned to
  a set/frozenset/tuple literal outside the canonical vocab module.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "magi_agent"

_KERNEL_FILE = "security/credential_vocab.py"


def _iter_module_files() -> list[Path]:
    return sorted(
        path
        for path in PACKAGE.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _is_credential_lease_regex_literal(node: ast.expr) -> bool:
    """True if ``node`` is a ``re.compile("...credential-lease:...")``-shaped
    call expression."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        if func.attr != "compile":
            return False
    elif isinstance(func, ast.Name):
        if func.id != "compile":
            return False
    else:
        return False
    if not node.args:
        return False
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return "credential-lease:" in first.value
    return False


def _forked_lease_regex_assignments() -> list[str]:
    offenders: list[str] = []
    for path in _iter_module_files():
        rel = path.relative_to(PACKAGE).as_posix()
        if rel == _KERNEL_FILE:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in tree.body:
            value: ast.expr | None
            targets: list[ast.expr]
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
                value = node.value
            else:
                continue
            if value is None:
                continue
            # 1. Identifier named ``_LEASE_RE`` / ``LEASE_REF_RE`` — caught
            #    regardless of value.
            for target in targets:
                if isinstance(target, ast.Name) and target.id in {
                    "_LEASE_RE",
                    "LEASE_REF_RE",
                }:
                    # Allow the canonical import-rebind (e.g.
                    # ``from .credential_vocab import LEASE_REF_RE as _LEASE_RE``)
                    # because that is implemented as an ``ImportFrom`` node,
                    # not an ``Assign`` — so reaching here means a literal
                    # regex assignment is happening.
                    offenders.append(rel)
                    break
            else:
                # 2. Anonymous regex literal containing the lease prefix.
                if _is_credential_lease_regex_literal(value):
                    offenders.append(rel)
    return offenders


_FROZEN_NAMES = {
    "_CREDENTIAL_QUERY_KEYS",
    "CREDENTIAL_QUERY_KEYS",
    "_SENSITIVE_LEASE_FRAGMENTS",
    "SENSITIVE_LEASE_FRAGMENTS",
}


def _forked_vocab_collection_assignments() -> list[tuple[str, str]]:
    offenders: list[tuple[str, str]] = []
    for path in _iter_module_files():
        rel = path.relative_to(PACKAGE).as_posix()
        if rel == _KERNEL_FILE:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in tree.body:
            targets: list[ast.expr]
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id in _FROZEN_NAMES:
                    offenders.append((rel, target.id))
                    break
    return offenders


def test_no_forked_lease_regex_outside_vocab_leaf() -> None:
    offenders = sorted(_forked_lease_regex_assignments())
    assert not offenders, (
        f"Forked ``credential-lease:`` regex outside {_KERNEL_FILE}. The "
        "lease-ref grammar MUST live in one place (C-9) so the issuer and "
        "validator cannot drift on what counts as a lease ref.\n"
        f"Offenders: {offenders}\n"
        "Fix: import from magi_agent.security.credential_vocab.LEASE_REF_RE."
    )


def test_no_forked_credential_vocab_collection_outside_vocab_leaf() -> None:
    offenders = sorted(_forked_vocab_collection_assignments())
    assert not offenders, (
        "Forked credential-vocab collection outside "
        f"{_KERNEL_FILE}:\n  "
        + "\n  ".join(f"{rel}:{name}" for rel, name in offenders)
        + "\nThe vocab MUST live in one place (C-9). Use:\n"
        "  from magi_agent.security.credential_vocab import "
        "CREDENTIAL_QUERY_KEYS, SENSITIVE_LEASE_FRAGMENTS"
    )


def test_vocab_kernel_exports_canonical_surface() -> None:
    """Catches accidental rename/removal of the canonical vocab symbols."""
    from magi_agent.security import credential_vocab

    assert hasattr(credential_vocab, "LEASE_REF_RE")
    assert hasattr(credential_vocab, "SENSITIVE_LEASE_FRAGMENTS")
    assert hasattr(credential_vocab, "CREDENTIAL_QUERY_KEYS")
    assert hasattr(credential_vocab, "looks_like_credential")
