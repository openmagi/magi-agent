"""I-2 PR A meta-test: each module-local ``_truthy`` / ``_truthy_env`` /
``_env_truthy`` / ``_env_enabled`` / ``_is_true`` env-reading helper is either
(a) a thin one-line delegation to the canonical
:mod:`magi_agent.config._truthy` (kept for an existing public-name import), or
(b) removed in favour of the shared helper.

Why
---
Before I-2, ~12 modules each re-implemented the same truthy parser. Two
hazards followed:

* The allowlist set (``{"1", "true", "yes", "on"}``) could drift from one
  copy to another (e.g. a typo'd / missing element).
* Some copies were *denylist* shaped (see ``test_truthy_convention_no_denylist``),
  conflicting with the allowlist copies under the same module-local name.

After I-2 PR A the per-module copies that remain MUST be thin delegation
wrappers around :func:`magi_agent.config._truthy.is_true` /
:func:`magi_agent.config._truthy.env_bool`, and only the wrappers that have
external callers (e.g. ``ops.health._truthy_env``) need to keep the public
name at all.

The test is intentionally name-scoped (not regex-based on the truthy set
literal) so it catches the *shape* (a module-local helper exists) regardless
of how the body is spelled. Once a wrapper delegates to the canonical leaf,
the literal set lives in exactly one place and the helper name is just an
alias.

Per-helper rules
----------------
* The wrapper body must import / reference one of:
  ``magi_agent.config._truthy.is_true``, ``env_bool``, ``env_bool_default_true``
  (or ``flags.flag_bool`` which itself delegates to the leaf).
* The wrapper body must NOT itself contain the truthy/falsey literal sets
  (``{"1", "true", "yes", "on"}`` or ``{"0", "false", "no", "off"}``).

The exhaustive list of allowed wrapper names (``_ALLOWED_WRAPPERS``) is the
shrinking ratchet — PR A pins it to the public-API wrappers that this PR
keeps as one-line delegates. Removing additional names here is a future-PR
cleanup.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "magi_agent"

# Canonical leaf — exempt; this is where the helpers actually live.
_TRUTHY_LEAF = "config/_truthy.py"

# Helper names this test is interested in (the historic per-module spellings).
_HELPER_NAMES: frozenset[str] = frozenset(
    {
        "_truthy",
        "_truthy_env",
        "_env_truthy",
        "_env_enabled",
        "_is_true",
        "_env_flag",
    }
)

# Allowlisted per-module wrappers that MAY exist after PR A. Each entry is
# (relative_path, helper_name) and MUST be a thin delegation to the canonical
# leaf. Two reasons something stays on this list:
#   1. The helper name is publicly imported elsewhere in the tree (renaming
#      it is a wider sweep).
#   2. The helper is a default-true / shape-distinct flavour that still
#      delegates internally (``env_bool_default_true``).
# Everything not on this list (and not in ``_DELETE_PENDING``) is forbidden.
_ALLOWED_WRAPPERS: frozenset[tuple[str, str]] = frozenset(
    {
        # Public-API wrapper consumed by transport.sse / streaming_chat_route /
        # event_adapter / shadow.gate5b4c3_live_runner_boundary. Body delegates
        # to config._truthy.is_true (one-line wrapper).
        ("ops/health.py", "_truthy_env"),
        # The four channels/*_live.py adapters do NOT define a module-local
        # truthy helper today (the denylist is inlined in each
        # ``is_live_*_enabled`` function body, see
        # ``test_truthy_convention_no_denylist`` which allowlists those four
        # files). PR B will convert them and may introduce a wrapper at that
        # point; until then the four channels files are absent from this set
        # by design.
    }
)

# Helper-name patterns that should not exist at all after PR A (module-local
# copies whose name is not part of any external import). Listed for clarity;
# the offender computation treats anything outside ``_ALLOWED_WRAPPERS`` as
# forbidden anyway. This set is informational.
_DELETE_PENDING: frozenset[str] = frozenset()


_TRUTHY_LITERAL_RE = re.compile(
    r"""\{\s*['"]1['"]\s*,\s*['"]true['"]\s*,\s*['"]yes['"]\s*,\s*['"]on['"]\s*\}""",
    re.DOTALL,
)
_FALSY_LITERAL_RE = re.compile(
    r"""\{\s*['"]0['"]\s*,\s*['"]false['"]\s*,\s*['"]no['"]\s*,\s*['"]off['"]\s*\}""",
    re.DOTALL,
)
_DELEGATION_RE = re.compile(
    r"""(magi_agent\.config\._truthy|config\._truthy|from\s+\.+_truthy|from\s+magi_agent\.config\._truthy|"""
    r"""flags\.flag_bool|config\.flags\.flag_bool|is_true|env_bool|env_bool_default_true)"""
)


def _iter_module_files() -> list[Path]:
    return sorted(
        path
        for path in PACKAGE.rglob("*.py")
        if "__pycache__" not in path.parts
        and "tests" not in path.parts
    )


def _function_looks_like_env_truthy(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Heuristic: does this helper parse a *string-truthy* value (the I-2
    concern) — vs. a peer helper that tests ``value is True`` or compares
    object identity (those are NOT env-truthy parsers and out of I-2 scope)?

    Two positive signals — either is sufficient:

    1. The body contains a comparison against the canonical truthy/falsey
       literal set (``in {"1", "true", "yes", "on"}`` etc.) OR a reference to
       a module-level ``_TRUE_VALUES`` / ``_TRUE_STRINGS`` / ``_FALSY``
       constant by name.
    2. The body reads ``os.environ.get(...)`` (env-reader-shaped).

    Helpers like ``tools/manifest.py:_truthy(value: object) -> bool:
    return value is True`` will NOT match because they have neither signal —
    they are object-identity checks, unrelated to truthy convention drift.
    """
    body_src = ast.unparse(node)
    # Truthy / falsey literal set (allow ``casefold``/``lower``-shaped lookups).
    if (
        '"1"' in body_src and '"true"' in body_src and '"yes"' in body_src and '"on"' in body_src
    ) or (
        "'1'" in body_src and "'true'" in body_src and "'yes'" in body_src and "'on'" in body_src
    ):
        return True
    if (
        '"0"' in body_src and '"false"' in body_src and '"no"' in body_src and '"off"' in body_src
    ) or (
        "'0'" in body_src and "'false'" in body_src and "'no'" in body_src and "'off'" in body_src
    ):
        return True
    # References to module-level truthy/falsey set constants by name.
    for name in (
        "_TRUE_VALUES",
        "_TRUE_STRINGS",
        "_TRUTHY",
        "_FALSY",
        "_FALSE_VALUES",
        "TRUE_VALUES",
        "FALSE_VALUES",
    ):
        if re.search(rf"\b{re.escape(name)}\b", body_src):
            return True
    # ``os.environ.get(...)`` shape.
    if "os.environ.get" in body_src or "environ.get" in body_src:
        return True
    return False


def _module_local_helper_defs(path: Path) -> list[tuple[str, str]]:
    """Return list of (relative_path, helper_name) defined at module level
    that *look like* env-truthy parsers (object-identity helpers excluded)."""
    rel = path.relative_to(PACKAGE).as_posix()
    if rel == _TRUTHY_LEAF:
        return []
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    hits: list[tuple[str, str]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in _HELPER_NAMES:
            continue
        if not _function_looks_like_env_truthy(node):
            continue
        hits.append((rel, node.name))
    return hits


def _function_body_source(source: str, name: str) -> str | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    return None


def test_no_module_local_truthy_helper_outside_allowlist() -> None:
    """After I-2 PR A, the only module-local truthy/env helpers that remain
    are the ones explicitly allowlisted above. Everything else delegates to
    the canonical leaf or has been deleted."""
    offenders: list[tuple[str, str]] = []
    for path in _iter_module_files():
        for rel, name in _module_local_helper_defs(path):
            if (rel, name) in _ALLOWED_WRAPPERS:
                continue
            offenders.append((rel, name))
    assert not offenders, (
        "Module-local truthy/env helpers exist outside the canonical leaf "
        "and outside the _ALLOWED_WRAPPERS ratchet. Each must either delegate "
        "to magi_agent.config._truthy (is_true / env_bool) or be removed in "
        "favour of the shared helper.\n"
        f"Offenders: {offenders}\n"
        "Fix: either rewrite the body as a one-line delegation and add the "
        "(path, name) tuple to _ALLOWED_WRAPPERS, or delete the helper and "
        "inline a call to env_bool() at the call sites."
    )


def test_allowed_wrappers_delegate_to_canonical_leaf() -> None:
    """The allowlisted wrappers must NOT spell their own truthy set literal —
    they must delegate to the canonical leaf so the literal lives in exactly
    one place. (PR B will tighten the channels copies similarly.)"""
    failures: list[str] = []
    for rel, name in _ALLOWED_WRAPPERS:
        path = PACKAGE / rel
        if not path.exists():
            failures.append(f"{rel}: missing file")
            continue
        source = path.read_text(encoding="utf-8")
        body = _function_body_source(source, name)
        if body is None:
            failures.append(f"{rel}:{name} not found")
            continue
        # Channels are PR B's responsibility — they still carry the literal
        # today and PR A defers the conversion.
        if rel.startswith("channels/"):
            continue
        if _TRUTHY_LITERAL_RE.search(body) or _FALSY_LITERAL_RE.search(body):
            failures.append(
                f"{rel}:{name} still contains a truthy/falsey literal set; "
                "must delegate to magi_agent.config._truthy"
            )
            continue
        if not _DELEGATION_RE.search(body) and not _DELEGATION_RE.search(source):
            failures.append(
                f"{rel}:{name} does not reference the canonical leaf; "
                "delegate to magi_agent.config._truthy.is_true / env_bool"
            )
    assert not failures, "\n  - " + "\n  - ".join(failures)
