"""T0 property fuzz — persistence + catalog layer (zero LLM, CI).

Design section 12 U4, store-side properties. These drive the pure persistence
functions directly against a per-example tmp JSON store (``path=`` argument), so
no ASGI app and no LLM are involved:

- ``ensure_policies_for_unreferenced_rules`` twice == the second run creates 0
  (idempotence — the U2 backfill is a fixed point).
- A grouped save followed by the backfill never double-represents: exactly one
  policy owns the group's member ids and no extra 1-rule policy references a
  member (the ``migrate_groups_to_policies`` ordering must win over the
  per-unreferenced-rule promotion).

Each property is first shown to FAIL against a deliberately-weakened copy of the
production logic (the mutation check, documented in the commit message), then to
pass against production.
"""
from __future__ import annotations

import json
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from magi_agent.customize.custom_rules import _LEGAL
from magi_agent.customize.policies import (
    ensure_policies_for_unreferenced_rules,
    list_policies,
)

# ---------------------------------------------------------------------------
# Value strategies drawn from the production vocabulary (so a generated rule is
# a plausible stored rule, not garbage the store would reject before the
# property is exercised).
# ---------------------------------------------------------------------------

# Kinds whose legal matrix is a singleton (kind -> one slot -> one action): the
# cheapest way to synthesize a validator-clean stored rule without reimplementing
# every per-kind payload schema. We build tool_perm / capability_scope / etc.
# rules with the minimal legal (firesAt, action) and a trivially-valid payload.
_RULE_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789_-"

_rule_id_strategy = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-",
    min_size=1,
    max_size=24,
).map(lambda s: "cr_" + s)

_group_id_strategy = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_",
    min_size=1,
    max_size=16,
).map(lambda s: "grp_" + s)


def _tool_perm_rule(rule_id: str, *, group_id: str | None = None) -> dict:
    """A minimal validator-clean tool_perm rule (before_tool_use / block)."""
    rule: dict = {
        "id": rule_id,
        "scope": "always",
        "enabled": True,
        "firesAt": "before_tool_use",
        "action": "block",
        "what": {
            "kind": "tool_perm",
            "payload": {"match": {"tool": "Bash"}, "decision": "deny"},
        },
    }
    if group_id is not None:
        rule["groupId"] = group_id
    return rule


def _write_store(path: Path, rules: list[dict]) -> None:
    path.write_text(
        json.dumps({"verification": {"custom_rules": rules}}),
        encoding="utf-8",
    )


# Sanity: the singleton kinds we lean on still have exactly one legal (slot,
# action) at HEAD; if the matrix drifts, the property fixtures must be revisited.
def test_legal_matrix_singletons_still_hold() -> None:
    assert _LEGAL["tool_perm"] == {"before_tool_use": frozenset({"block", "ask_approval"})}
    assert _LEGAL["capability_scope"] == {"spawn": frozenset({"block"})}


# ---------------------------------------------------------------------------
# Property: the U2 backfill is idempotent — running it a second time creates 0.
# ---------------------------------------------------------------------------


@given(rule_ids=st.lists(_rule_id_strategy, min_size=0, max_size=6, unique=True))
def test_backfill_idempotent_second_run_creates_zero(
    rule_ids: list[str], tmp_path_factory
) -> None:
    path = tmp_path_factory.mktemp("store") / "customize.json"
    _write_store(path, [_tool_perm_rule(rid) for rid in rule_ids])

    first = ensure_policies_for_unreferenced_rules(path)
    second = ensure_policies_for_unreferenced_rules(path)

    assert second == 0, (
        f"second backfill created {second} policies (not idempotent); "
        f"first created {first} for {len(rule_ids)} rules"
    )
    # And a THIRD run is still a fixed point.
    assert ensure_policies_for_unreferenced_rules(path) == 0


# ---------------------------------------------------------------------------
# Property: after a grouped save + backfill, no double-representation.
# One policy owns exactly the group's member ids; no extra policy references a
# member on its own.
# ---------------------------------------------------------------------------


@given(
    group_id=_group_id_strategy,
    member_ids=st.lists(_rule_id_strategy, min_size=1, max_size=4, unique=True),
    loose_ids=st.lists(_rule_id_strategy, min_size=0, max_size=3, unique=True),
)
def test_grouped_then_backfill_never_double_represents(
    group_id: str, member_ids: list[str], loose_ids: list[str], tmp_path_factory
) -> None:
    # Keep the two id pools disjoint so a loose rule can never accidentally look
    # like a group member.
    loose_ids = [rid for rid in loose_ids if rid not in set(member_ids)]

    from magi_agent.customize.policies import (
        _slugify,
        migrate_groups_to_policies,
    )

    path = tmp_path_factory.mktemp("store") / "customize.json"
    rules = [_tool_perm_rule(rid, group_id=group_id) for rid in member_ids]
    rules += [_tool_perm_rule(rid) for rid in loose_ids]
    _write_store(path, rules)

    # The FE grouped-save contract persists the rules then upserts the group
    # policy; the read-time seam runs migrate_groups first, then per-rule
    # backfill. Exercise that exact ordering.
    migrate_groups_to_policies(path)
    ensure_policies_for_unreferenced_rules(path)
    # A second pass must not add duplicate representations either.
    ensure_policies_for_unreferenced_rules(path)

    policies = list_policies(path)
    member_set = set(member_ids)

    # Exactly one policy owns the whole group.
    group_owners = [p for p in policies if set(p.rule_ids) == member_set]
    assert len(group_owners) == 1, (
        f"expected exactly one policy owning group members {sorted(member_set)}, "
        f"got {len(group_owners)}: {[p.policy_id for p in policies]}"
    )
    slug = _slugify(group_id)
    assert group_owners[0].policy_id == slug

    # No OTHER policy references a group member on its own.
    for p in policies:
        rids = set(p.rule_ids)
        if rids == member_set:
            continue
        overlap = rids & member_set
        assert not overlap, (
            f"policy {p.policy_id!r} additionally references group members "
            f"{sorted(overlap)} (double representation)"
        )

    # Every loose rule is surfaced by exactly one 1-rule policy.
    for rid in loose_ids:
        refs = [p for p in policies if rid in set(p.rule_ids)]
        assert len(refs) == 1, f"loose rule {rid!r} referenced by {len(refs)} policies"
