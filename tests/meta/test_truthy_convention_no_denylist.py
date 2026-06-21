"""I-2 PR A meta-test: forbid the dangerous DENYLIST truthy convention outside
an explicit, shrinking allowlist.

Two truthy conventions coexist in the tree:

* **Allowlist**: ``value.strip().lower() in {"1", "true", "yes", "on"}`` — the
  canonical, opt-in semantic shared by :mod:`magi_agent.config._truthy`
  (``is_true`` / ``env_bool``). Unknown / mis-typed / non-truthy values resolve
  to ``False`` so a typo cannot enable a side-effect gate.
* **Denylist**: ``value.strip().lower() not in {"0", "false", "no", "off"}`` —
  the dangerous semantic where ANY non-empty, non-explicitly-falsey value
  (e.g. ``"disabled"`` / ``"random_garbage"``) silently *enables* the gate.

This test scans the package and asserts that the denylist literal appears ONLY
in the channels/*_live.py adapters that PR A defers to PR B (per the
``2026-06-18-magi-agent-oss-main-remediation/ws-I-config-quality.md`` plan,
"ship channels-live conversions in their own PR"). When PR B lands, the
allowlist below shrinks to the empty set and the literal is gone from the tree.

AST-based for the ``not in <set>`` shape, with a fallback regex for the
``_FALSY`` constant alias spelling.

The single source of truth ``magi_agent/config/_truthy.py`` is excluded
because it defines the canonical ``FALSE_VALUES`` constant that all readers
should delegate to.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "magi_agent"

# Shrinking allowlist: I-2 PR A explicitly defers the four stage-3 live-channel
# adapters to PR B (separate behaviour test + PR per plan). When PR B lands,
# this set goes to empty and the meta-test ratchets to zero.
_PR_B_DEFERRED: frozenset[str] = frozenset(
    {
        "channels/telegram_live.py",
        "channels/slack_live.py",
        "channels/email_live.py",
        "channels/discord_live.py",
    }
)

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
    """Only the four channels/*_live.py adapters (deferred to PR B) may keep
    the dangerous denylist semantic. Everything else MUST use the allowlist
    helper :func:`magi_agent.config._truthy.env_bool` / ``is_true`` so a
    mis-configured value cannot silently enable a gate."""
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


def test_pr_b_allowlist_ratchet_does_not_widen() -> None:
    """Guard against accidentally re-introducing the denylist outside the four
    channels adapters PR A defers. If a future change adds a denylist back
    elsewhere, the previous test fails; this one ensures the deferred set
    cannot quietly *grow* without an explicit edit to the allowlist."""
    offenders = set(_denylist_offenders())
    extra = offenders - _PR_B_DEFERRED
    assert not extra, (
        "Allowlist ratchet failed: new denylist offenders appeared outside "
        f"PR B's deferred set. New offenders: {sorted(extra)}.\n"
        "Either fix them to the allowlist semantic, or — if there is a "
        "deliberate reason — extend _PR_B_DEFERRED in this test (requires "
        "review). Do not widen silently."
    )
