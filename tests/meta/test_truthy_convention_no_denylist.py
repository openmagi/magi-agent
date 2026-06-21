"""I-2 PR B meta-test: forbid the dangerous DENYLIST truthy convention
anywhere in :mod:`magi_agent` outside the canonical ``_truthy`` leaf.

Two truthy conventions used to coexist in the tree:

* **Allowlist**: ``value.strip().lower() in {"1", "true", "yes", "on"}`` — the
  canonical, opt-in semantic shared by :mod:`magi_agent.config._truthy`
  (``is_true`` / ``env_bool``). Unknown / mis-typed / non-truthy values resolve
  to ``False`` so a typo cannot enable a side-effect gate.
* **Denylist**: ``value.strip().lower() not in {"0", "false", "no", "off"}`` —
  the dangerous semantic where ANY non-empty, non-explicitly-falsey value
  (e.g. ``"disabled"`` / ``"random_garbage"``) silently *enables* the gate.

I-2 PR A converted every non-channel denylist site and left the four
``channels/*_live.py`` adapters on a shrinking allowlist (stage-3 live
side-effect — its own PR + behaviour test, per the plan
``2026-06-18-magi-agent-oss-main-remediation/ws-I-config-quality.md``).

I-2 PR B converts those four channels and **shrinks the allowlist to empty**.
The dangerous literal is now banned everywhere in :mod:`magi_agent` outside
the canonical ``_truthy.py`` leaf (which is the one legitimate place that
names the ``FALSE_VALUES`` set as a constant that other readers delegate to).

AST-based for the ``not in <set>`` shape, with a fallback regex for the
``_FALSY`` constant alias spelling.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "magi_agent"

# Shrinking allowlist — I-2 PR B landed and converted the last four sites
# (channels/{telegram,slack,email,discord}_live.py); the allowlist is now
# empty. The dangerous denylist literal is banned everywhere in
# :mod:`magi_agent` outside the canonical ``_truthy.py`` leaf. Any future
# regression that re-introduces it (anywhere) fails ``test_no_denylist_...``.
_PR_B_DEFERRED: frozenset[str] = frozenset()

# The canonical truthy leaf is the one legitimate place that names the
# denylist set — as the ``FALSE_VALUES`` constant that all readers delegate to.
_TRUTHY_LEAF = "config/_truthy.py"

_FALSY_NAMES = {"0", "false", "no", "off"}


def _iter_module_files() -> list[Path]:
    return sorted(
        path
        for path in PACKAGE.rglob("*.py")
        if "__pycache__" not in path.parts
        and "tests" not in path.parts
    )


def _is_denylist_set_literal(node: ast.expr) -> bool:
    """True if ``node`` is a set / frozenset literal whose elements are exactly
    the falsey strings ``{"0", "false", "no", "off"}`` (order-insensitive)."""
    if isinstance(node, ast.Set):
        elements = node.elts
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "frozenset"
        and len(node.args) == 1
        and isinstance(node.args[0], (ast.Set, ast.List, ast.Tuple))
    ):
        elements = node.args[0].elts
    else:
        return False
    values: set[str] = set()
    for el in elements:
        if not (isinstance(el, ast.Constant) and isinstance(el.value, str)):
            return False
        values.add(el.value.lower())
    return values == _FALSY_NAMES


def _has_denylist_compare(node: ast.AST) -> bool:
    """AST visit: any ``X not in <denylist-literal>`` comparison."""
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Compare):
            continue
        for op, comparator in zip(sub.ops, sub.comparators):
            if isinstance(op, ast.NotIn) and _is_denylist_set_literal(comparator):
                return True
    return False


# Fallback for the ``_FALSY = {...}`` constant pattern where the comparison is
# spelled ``val not in _FALSY``. Catches the constant assignment too so that
# even a "lift the literal into a module constant" workaround is flagged.
_FALSY_CONST_RE = re.compile(
    r"""^\s*_FALSY\s*[:=]\s*(frozenset\(\s*)?\{[^}]+\}""",
    re.MULTILINE,
)


def _denylist_offenders() -> list[str]:
    offenders: list[str] = []
    for path in _iter_module_files():
        rel = path.relative_to(PACKAGE).as_posix()
        if rel == _TRUTHY_LEAF:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        # 1. AST: ``X not in {"0", "false", "no", "off"}`` (any nesting).
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            tree = None
        has_offence = False
        if tree is not None and _has_denylist_compare(tree):
            has_offence = True
        # 2. Constant alias: ``_FALSY = {"0", "false", "no", "off"}``.
        if not has_offence and _FALSY_CONST_RE.search(source):
            # Confirm the literal set matches the denylist exactly.
            if all(token in source for token in ('"0"', '"false"', '"no"', '"off"')) or all(
                token in source for token in ("'0'", "'false'", "'no'", "'off'")
            ):
                has_offence = True
        if has_offence:
            offenders.append(rel)
    return sorted(set(offenders))


def test_no_denylist_truthy_outside_pr_b_allowlist() -> None:
    """The dangerous denylist truthy convention is banned everywhere in
    :mod:`magi_agent` outside the canonical ``config/_truthy.py`` leaf.

    Every reader MUST use the allowlist helper
    :func:`magi_agent.config._truthy.env_bool` / ``is_true`` so a
    mis-configured value cannot silently enable a gate.
    """
    offenders = _denylist_offenders()
    unexpected = sorted(set(offenders) - _PR_B_DEFERRED)
    assert not unexpected, (
        "Modules use the dangerous denylist truthy convention "
        'X not in {"0", "false", "no", "off"} (or a _FALSY alias). This '
        "semantic silently ENABLES gates on any unknown / non-empty value "
        '(e.g. MAGI_X="disabled" reads as ON).\n'
        f"Offenders: {unexpected}\n"
        "Fix: replace with magi_agent.config._truthy.env_bool(env, NAME, "
        "default=...) (allowlist semantics, opt-in)."
    )


def test_pr_b_allowlist_ratchet_is_empty() -> None:
    """The PR B ratchet is now empty: no module is allowed to keep the
    dangerous denylist semantic. This complements
    ``test_no_denylist_truthy_outside_pr_b_allowlist`` (which checks current
    offenders) by guarding the ratchet itself — the constant
    :data:`_PR_B_DEFERRED` must remain ``frozenset()`` and any attempt to
    re-add an entry to it has to confront this test (which forces an
    explicit review of why the denylist is being reintroduced).
    """
    assert _PR_B_DEFERRED == frozenset(), (
        "The I-2 PR B ratchet is meant to stay empty: every channels/*_live.py "
        "adapter was converted to the canonical allowlist. Adding entries back "
        f"({sorted(_PR_B_DEFERRED)}) re-permits the dangerous denylist "
        'semantic where MAGI_X="disabled" silently ENABLES a gate. If a '
        "future PR genuinely needs to defer one channel during a migration, "
        "this assertion + the meta-test above must be relaxed in the SAME PR "
        "and reviewed jointly."
    )
