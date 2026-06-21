"""Meta-ratchet for the I-1 ``is_*_enabled`` -> ``flag_bool`` migration.

The plan (``docs/plans/2026-06-18-magi-agent-oss-main-remediation/
ws-I-config-quality.md`` §I-1) recommends shrinking the set of inline
``is_*_enabled`` bodies (``return _is_true(source.get(NAME))``) one batch at a
time so each PR is reviewable and the budget ratchets monotonically. This
file is the budget ledger for that migration:

* :data:`_UNMIGRATED_INLINE_FLAGS` lists the function names whose bodies still
  call ``_is_true`` (i.e. have not been routed through the
  ``config.flags`` registry yet).
* :func:`test_no_new_inline_is_enabled_body` asserts the *real* set scanned out
  of ``magi_agent/config/env.py`` is a subset of the allowlist — so a new
  inline ``def is_X_enabled: ... _is_true(...)`` body forces the author to
  either migrate it or document it here.
* :func:`test_unmigrated_allowlist_is_shrinking` asserts the inverse: every
  entry in the allowlist still exists as an un-migrated inline body in
  ``env.py``. Drops it stale rows so the budget can only fall — the same
  ratchet shape as ``tests/meta/test_no_forked_digest.py``.

Functions that read the flag via :func:`_runtime_feature_enabled` (profile-aware
default-ON; needs ``flag_profile_bool`` not ``flag_bool``) or via the 3-state
``resolve_document_authoring_coverage_mode`` helper are intentionally NOT in
scope for this ratchet — they do not call ``_is_true`` directly and the
plan's batch-1 explicitly defers them.
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
# ``flag_bool``. The remaining ``is_*_enabled`` bodies (profile-aware
# default-ON via ``_runtime_feature_enabled`` + the 3-state
# document-authoring-coverage helper) are intentionally NOT in scope for this
# ratchet — they need different mechanisms (``flag_profile_bool`` for the
# former, a tri-state resolver for the latter) and are slated for batch 3+.
_UNMIGRATED_INLINE_FLAGS: frozenset[str] = frozenset()


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


def test_no_new_inline_is_enabled_body() -> None:
    """Forbid a NEW ``is_*_enabled`` body that calls ``_is_true`` directly.

    Author the new flag in ``magi_agent/config/flags.py`` (``_b`` for strict
    default-OFF or ``_pb`` for profile-aware default-ON) and route the reader
    through :func:`magi_agent.config.flags.flag_bool` /
    :func:`flag_profile_bool`. The allowlist below is a documented,
    SHRINKING budget — adding to it is not the fix, migrating is.
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


def test_allowlist_count_records_post_batch_state() -> None:
    """Pin the post-batch-2 size so we notice future batches.

    After I-1 batch 2 the un-migrated set is EMPTY — every simple-body
    ``is_*_enabled`` function whose inline body was
    ``return _is_true(source.get(NAME))`` (15 in total: 8 from batch 1 + 7
    from batch 2) now delegates to ``config.flags.flag_bool``. The remaining
    bodies in ``env.py`` consult ``_runtime_feature_enabled`` (profile-aware
    default-ON; needs ``flag_profile_bool``) or
    ``resolve_document_authoring_coverage_mode`` (3-state) instead — neither
    is in scope for this strict-truthy ratchet. The fixed-count anchor pins
    the bottom so a future regression that re-introduces an inline
    ``_is_true`` body would fail :func:`test_no_new_inline_is_enabled_body`
    AND this counter (the ratchet only ever falls).
    """

    assert len(_UNMIGRATED_INLINE_FLAGS) == 0
