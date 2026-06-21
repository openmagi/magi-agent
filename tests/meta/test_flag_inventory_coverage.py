"""Meta-ratchet for the I-1 ``is_*_enabled`` -> ``flag_bool`` migration.

The plan (``docs/plans/2026-06-18-magi-agent-oss-main-remediation/
ws-I-config-quality.md`` §I-1) recommends shrinking the set of inline
``is_*_enabled`` bodies one batch at a time so each PR is reviewable and the
budget ratchets monotonically. This file is the budget ledger for that
migration and now covers **both** inline patterns under a single unified
inventory:

* Strict default-OFF inline bodies — ``return _is_true(source.get(NAME))``
  (or its ``_is_true(env.get(NAME))`` cousin). Migrate via
  :func:`magi_agent.config.flags.flag_bool` (register a ``_b(...)`` FlagSpec).
* Profile-aware default-ON inline bodies —
  ``return _runtime_feature_enabled(source, NAME)``. Migrate via
  :func:`magi_agent.config.flags.flag_profile_bool` (register a ``_pb(...)``
  FlagSpec).

The plan's batch-1 / batch-2 / batch-3 sequence drives both allowlists to
empty:

* :data:`_UNMIGRATED_INLINE_FLAGS` — strict default-OFF inline bodies. EMPTY
  after batch 2.
* :data:`_UNMIGRATED_PROFILE_AWARE_INLINE_FLAGS` — profile-aware default-ON
  inline bodies. EMPTY after batch 3 (this PR migrates the last 6).

:func:`test_no_new_inline_is_enabled_body` asserts the *real* set of
strict-default-OFF inline bodies scanned out of ``magi_agent/config/env.py``
is a subset of :data:`_UNMIGRATED_INLINE_FLAGS`, and the parallel
:func:`test_no_new_profile_aware_inline_body` asserts the inverse for the
profile-aware family. New inline bodies of either shape force the author to
either migrate or document — but the allowlists never grow, only shrink.

:func:`test_unmigrated_allowlist_is_shrinking` /
:func:`test_unmigrated_profile_aware_allowlist_is_shrinking` are the inverse
ratchets: every listed name must still be inline in ``env.py``. Stale rows
fail the gate so the budget can only fall — the same shape as
``tests/meta/test_no_forked_digest.py``.

The 3-state ``resolve_document_authoring_coverage_mode`` helper is
intentionally NOT in scope for either ratchet — it is neither strict-truthy
nor profile-aware, and reaches the registry through a dedicated tri-state
resolver instead.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_PY = _REPO_ROOT / "magi_agent" / "config" / "env.py"
_IS_ENABLED_RE = re.compile(r"^is_[a-z0-9_]+_enabled$")


# Shrinking allowlist: every entry is an ``is_*_enabled`` function whose body
# still calls ``_is_true(...)`` directly (i.e. has not been routed through the
# ``config.flags`` registry yet). Drive this set toward empty one batch at a
# time per the plan §I-1 ratchet. After I-1 batch 2 the allowlist is EMPTY —
# all 15 simple-body strict default-OFF flags (8 from batch 1: prompt /
# coding-context / key-aware / tool-usage-guidance; 7 from batch 2: the
# strict-default-OFF master-switch gates including grounded-answer-guard,
# goal-nudge, research-fact-guidance, facts-replan, user-hooks,
# dashboard-pack-authoring, tool-synthesis-nudge) now delegate to
# ``flag_bool``. The profile-aware default-ON inline bodies live in a
# sibling allowlist below — driven empty by batch 3.
_UNMIGRATED_INLINE_FLAGS: frozenset[str] = frozenset()


# Sibling shrinking allowlist for the *profile-aware default-ON* inline
# pattern: every entry is an ``is_*_enabled`` function whose body still calls
# ``_runtime_feature_enabled(env, NAME)`` directly. After I-1 batch 3 (this
# PR) all 6 such bodies — read-ledger / self-introspection / evidence-ledger-
# lifecycle / format-on-write / read-quality / message-cache — delegate to
# :func:`magi_agent.config.flags.flag_profile_bool` and this allowlist is
# EMPTY. The other ``_runtime_feature_enabled`` readers in ``env.py``
# (``apply_patch_enabled`` / ``ripgrep_enabled`` / etc.) are not
# ``is_*_enabled``-named so the scanner already ignores them; if they are
# ever renamed to fit ``is_*_enabled`` they will need to be migrated too.
_UNMIGRATED_PROFILE_AWARE_INLINE_FLAGS: frozenset[str] = frozenset()


def _scan_inline_is_enabled_bodies() -> set[str]:
    """Return the set of ``is_*_enabled`` functions in ``env.py`` whose body
    still contains a direct ``_is_true(...)`` call.

    Uses :mod:`ast` so docstring mentions of ``_is_true`` (e.g. the migration
    comment ``"byte-identical to _is_true(source.get(...))"``) do NOT count —
    only real call nodes do.
    """

    tree = ast.parse(_ENV_PY.read_text(encoding="utf-8"))
    inline: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if not _IS_ENABLED_RE.match(node.name):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            fn = sub.func
            # Bare name: ``_is_true(...)``.
            if isinstance(fn, ast.Name) and fn.id == "_is_true":
                inline.add(node.name)
                break
            # Attribute: ``somemod._is_true(...)``.
            if isinstance(fn, ast.Attribute) and fn.attr == "_is_true":
                inline.add(node.name)
                break
    return inline


def _scan_inline_profile_aware_bodies() -> set[str]:
    """Return the set of ``is_*_enabled`` functions in ``env.py`` whose body
    still contains a direct ``_runtime_feature_enabled(...)`` call.

    AST-based for the same reason as the strict-default-OFF scanner above:
    docstring mentions of the helper do not count, only real call nodes.
    Covers both the bare ``_runtime_feature_enabled(env, NAME)`` form and the
    aliased ``somemod._runtime_feature_enabled(...)`` attribute form so a
    future ``from .env import _runtime_feature_enabled as foo`` rebinding
    cannot launder past the gate.
    """

    tree = ast.parse(_ENV_PY.read_text(encoding="utf-8"))
    inline: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if not _IS_ENABLED_RE.match(node.name):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            fn = sub.func
            if isinstance(fn, ast.Name) and fn.id == "_runtime_feature_enabled":
                inline.add(node.name)
                break
            if isinstance(fn, ast.Attribute) and fn.attr == "_runtime_feature_enabled":
                inline.add(node.name)
                break
    return inline


def test_no_new_inline_is_enabled_body() -> None:
    """Forbid a NEW ``is_*_enabled`` body that calls ``_is_true`` directly.

    Author the new flag in ``magi_agent/config/flags.py`` (``_b`` for strict
    default-OFF) and route the reader through
    :func:`magi_agent.config.flags.flag_bool`. The allowlist below is a
    documented, SHRINKING budget — adding to it is not the fix, migrating is.
    """

    found = _scan_inline_is_enabled_bodies()
    new = sorted(found - _UNMIGRATED_INLINE_FLAGS)
    assert not new, (
        "New inline `is_*_enabled` body found that still calls `_is_true(...)`. "
        "Migrate via `config.flags.flag_bool` (register a `_b(...)` FlagSpec) or "
        "`flag_profile_bool` for profile-aware default-ON gates. Do NOT add to "
        "_UNMIGRATED_INLINE_FLAGS — that allowlist is a shrinking ratchet.\n"
        + "\n".join(f"  - {name}" for name in new)
    )


def test_no_new_profile_aware_inline_body() -> None:
    """Forbid a NEW ``is_*_enabled`` body that calls ``_runtime_feature_enabled``.

    Author the new profile-aware default-ON flag in
    ``magi_agent/config/flags.py`` (``_pb(...)`` FlagSpec) and route the
    reader through :func:`magi_agent.config.flags.flag_profile_bool`. The
    allowlist is a documented, SHRINKING budget — adding to it is not the
    fix, migrating is.
    """

    found = _scan_inline_profile_aware_bodies()
    new = sorted(found - _UNMIGRATED_PROFILE_AWARE_INLINE_FLAGS)
    assert not new, (
        "New inline `is_*_enabled` body found that still calls "
        "`_runtime_feature_enabled(...)`. Migrate via "
        "`config.flags.flag_profile_bool` (register a `_pb(...)` FlagSpec). "
        "Do NOT add to _UNMIGRATED_PROFILE_AWARE_INLINE_FLAGS — that "
        "allowlist is a shrinking ratchet.\n"
        + "\n".join(f"  - {name}" for name in new)
    )


def test_unmigrated_allowlist_is_shrinking() -> None:
    """Inverse ratchet: every allowlist entry must still be inline in ``env.py``.

    Catches the case where a future batch migrates one of the listed
    functions but the author forgets to drop the corresponding entry from
    :data:`_UNMIGRATED_INLINE_FLAGS`. The allowlist is intended to fall
    monotonically — the same shape as
    ``tests/meta/test_no_forked_digest.py::test_frozen_base_allowlist_is_shrinking``.
    """

    found = _scan_inline_is_enabled_bodies()
    stale = sorted(_UNMIGRATED_INLINE_FLAGS - found)
    assert not stale, (
        "Stale `_UNMIGRATED_INLINE_FLAGS` entries (the listed `is_*_enabled` "
        "either no longer exists in env.py or has already been migrated to "
        "`flag_bool`) — remove from the allowlist so the ratchet stays tight.\n"
        + "\n".join(f"  - {name}" for name in stale)
    )


def test_unmigrated_profile_aware_allowlist_is_shrinking() -> None:
    """Inverse ratchet for the profile-aware sibling allowlist.

    Same shape as :func:`test_unmigrated_allowlist_is_shrinking` but pinned
    to :data:`_UNMIGRATED_PROFILE_AWARE_INLINE_FLAGS` and the
    ``_runtime_feature_enabled`` scanner.
    """

    found = _scan_inline_profile_aware_bodies()
    stale = sorted(_UNMIGRATED_PROFILE_AWARE_INLINE_FLAGS - found)
    assert not stale, (
        "Stale `_UNMIGRATED_PROFILE_AWARE_INLINE_FLAGS` entries (the listed "
        "`is_*_enabled` either no longer exists in env.py or has already been "
        "migrated to `flag_profile_bool`) — remove from the allowlist so the "
        "ratchet stays tight.\n"
        + "\n".join(f"  - {name}" for name in stale)
    )


def test_allowlist_count_records_post_batch_state() -> None:
    """Pin the post-batch-3 size of BOTH allowlists so future batches notice.

    After I-1 batch 3 both un-migrated sets are EMPTY:

    * 15 simple-body strict default-OFF ``is_*_enabled`` flags (8 from batch
      1: prompt / coding-context / key-aware / tool-usage-guidance; 7 from
      batch 2: the strict-default-OFF master-switch gates) delegate to
      ``flag_bool``.
    * 6 profile-aware default-ON ``is_*_enabled`` flags (this batch:
      read-ledger / self-introspection / evidence-ledger-lifecycle /
      format-on-write / read-quality / message-cache) delegate to
      ``flag_profile_bool``.

    Total 21 ``is_*_enabled`` readers now go through the registry. The
    fixed-count anchors pin the bottom so a future regression that
    re-introduces an inline body of either pattern would fail
    :func:`test_no_new_inline_is_enabled_body` /
    :func:`test_no_new_profile_aware_inline_body` AND this counter (the
    ratchet only ever falls).
    """

    assert len(_UNMIGRATED_INLINE_FLAGS) == 0
    assert len(_UNMIGRATED_PROFILE_AWARE_INLINE_FLAGS) == 0
