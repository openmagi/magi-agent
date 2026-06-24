"""E-3 — every model the ``ModelTierRegistry`` can emit has an explicit
context-window in :data:`_KNOWN_TOKEN_LIMITS`.

The bug E-3 fixes: ``runtime/model_tiers.py`` could surface a flagship
model id (e.g. ``claude-opus-4-8``) that ``_KNOWN_TOKEN_LIMITS.get(model)``
did not know — the lookup fell back to ``_DEFAULT_CONTEXT_WINDOW =
150_000`` and the most-used model got the most-conservative window,
firing compaction early and mis-warning budgets. REVIEW-A
engine H3 / llm H3 (E-3) asked for the missing entries to be added
AND a meta-test that asserts every registry-emitted model has a window.

The table additions already landed under earlier work; this module locks
the invariant so a future registry change that adds a model without
updating the window table fails the test loudly instead of silently
shipping the 150k default.
"""

from __future__ import annotations

from magi_agent.context._token_window_table import _KNOWN_TOKEN_LIMITS
from magi_agent.runtime.model_tiers import ModelTierRegistry


def _emit_candidate_model_ids(registry: ModelTierRegistry) -> set[str]:
    """Walk every ``ModelTierRecord`` the registry holds and collect any
    attribute that names a model id the runtime might surface. We are
    deliberately permissive over attribute names because the record
    schema has historically grown new fields (``model`` / ``model_id`` /
    ``id`` / ``name``) — the invariant must catch any of them."""

    seen: set[str] = set()
    for record in registry._records.values():
        for attr in ("model", "model_id", "id", "name"):
            value = getattr(record, attr, None)
            if isinstance(value, str) and value:
                seen.add(value)
    return seen


def test_every_registry_model_has_a_context_window() -> None:
    registry = ModelTierRegistry.with_defaults()
    candidates = _emit_candidate_model_ids(registry)
    # A safety check on the harness itself: the registry must yield at
    # least the flagship tier — an empty set would silently pass below.
    assert candidates, (
        "ModelTierRegistry.with_defaults() emitted no model ids — the "
        "introspection in this test is out of date or the registry "
        "schema changed"
    )
    missing = sorted(m for m in candidates if m not in _KNOWN_TOKEN_LIMITS)
    assert missing == [], (
        "Every model id that ModelTierRegistry can emit must have an "
        "explicit entry in ``_KNOWN_TOKEN_LIMITS`` (E-3): without one "
        "the lookup falls back to the 150k default and the most-used "
        f"flagship may silently fire compaction early. Missing: {missing}"
    )


def test_flagship_claude_opus_resolves_to_opus_window() -> None:
    """E-3's headline case: ``claude-opus-4-8`` must NOT inherit the
    150k *fallback default* by coincidence — it must be an *explicit*
    entry in the table so a future default change cannot silently
    re-introduce the bug."""

    assert "claude-opus-4-8" in _KNOWN_TOKEN_LIMITS
    assert "anthropic/claude-opus-4-8" in _KNOWN_TOKEN_LIMITS
    # And the value matches the Opus-class sibling.
    assert (
        _KNOWN_TOKEN_LIMITS["claude-opus-4-8"]
        == _KNOWN_TOKEN_LIMITS["claude-opus-4-6"]
    )
