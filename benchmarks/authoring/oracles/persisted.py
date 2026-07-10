"""Persisted-state oracle helpers — the strongest oracles.

All read through a ``PersistedSnapshot`` (the customize.json store + the
``GET /v1/app/policies`` and ``GET /v1/app/customize`` bodies + producer
sidecar) and raise ``OracleFailure`` with a named code on violation. See design
section 7. Every assertion is exact; there is no fuzzy matching.

These reuse the production ``validate_custom_rule`` so "the stored rule is
clean" means clean by the runtime's own definition.
"""
from __future__ import annotations

from typing import Any

from magi_agent.customize.custom_rules import validate_custom_rule

from benchmarks.authoring.adapter import PersistedSnapshot, SaveResult

_VALID_ENABLED_STATES = {"on", "off", "mixed", "managed"}
_VALID_SOURCES = {"builtinPolicy", "policy", "controlPlane"}
_RESERVED_IDS = ("source_citation", "verify_before_replying")


class OracleFailure(AssertionError):
    """A persisted-state oracle assertion failed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code


def _store_rules(snap: PersistedSnapshot) -> list[dict[str, Any]]:
    return [
        r
        for r in snap.store.get("verification", {}).get("custom_rules", [])
        if isinstance(r, dict)
    ]


def _find_rule(snap: PersistedSnapshot, rule_id: str) -> dict[str, Any] | None:
    for r in _store_rules(snap):
        if r.get("id") == rule_id:
            return r
    return None


def _policies_list(snap: PersistedSnapshot) -> list[dict[str, Any]]:
    return [
        p
        for p in (snap.policies.get("policies") or [])
        if isinstance(p, dict)
    ]


def _user_policies(snap: PersistedSnapshot) -> list[dict[str, Any]]:
    return [p for p in _policies_list(snap) if p.get("origin") == "user"]


def _catalog_policies(snap: PersistedSnapshot) -> list[dict[str, Any]]:
    cat = snap.customize.get("catalog") if isinstance(snap.customize, dict) else None
    if not isinstance(cat, dict):
        return []
    return [p for p in (cat.get("policies") or []) if isinstance(p, dict)]


# ---------------------------------------------------------------------------


def assert_rule_clean(snap: PersistedSnapshot, rule_id: str) -> None:
    """Stored rule exists, validates clean, and carries no policy-envelope keys."""
    rule = _find_rule(snap, rule_id)
    if rule is None:
        raise OracleFailure("rule_missing", f"rule {rule_id!r} not in store")
    issues = validate_custom_rule(rule)
    if issues:
        raise OracleFailure("rule_invalid", f"rule {rule_id!r} invalid: {issues}")
    for env_key in ("displayName", "intent"):
        if env_key in rule:
            raise OracleFailure(
                "envelope_not_stripped",
                f"rule {rule_id!r} kept policy-envelope key {env_key!r}",
            )


def assert_policy_intent(
    snap: PersistedSnapshot,
    rule_id: str,
    expected_intent: str,
    expected_display: str | None = None,
) -> None:
    """Exactly one USER policy references ``rule_id``; its intent matches."""
    refs = [p for p in _user_policies(snap) if rule_id in (p.get("ruleIds") or [])]
    if len(refs) != 1:
        raise OracleFailure(
            "intent_ref_count",
            f"expected exactly one user policy referencing {rule_id!r}, got {len(refs)}",
        )
    policy = refs[0]
    if policy.get("intent") != expected_intent:
        raise OracleFailure(
            "intent_mismatch",
            f"policy intent {policy.get('intent')!r} != expected {expected_intent!r}",
        )
    if expected_display is not None and policy.get("displayName") != expected_display:
        raise OracleFailure(
            "display_mismatch",
            f"displayName {policy.get('displayName')!r} != expected {expected_display!r}",
        )


def assert_no_orphan_rules(snap: PersistedSnapshot) -> None:
    """Every stored custom-rule id is referenced by some policy's ruleIds.

    The snapshot was taken AFTER a GET /v1/app/customize, which runs the
    idempotent U2 backfill, so an unreferenced rule here is a genuine orphan.
    """
    referenced: set[str] = set()
    for p in _policies_list(snap):
        for rid in p.get("ruleIds") or []:
            referenced.add(rid)
    for rule in _store_rules(snap):
        rid = rule.get("id")
        if rid and rid not in referenced:
            raise OracleFailure("orphan_rule", f"rule {rid!r} referenced by no policy")


def assert_no_double_representation(snap: PersistedSnapshot, group_id: str) -> None:
    """Exactly one policy's ruleIds equals the group's members; no extra 1-rule
    policy references any member."""
    grouped_rules = [
        r.get("id")
        for r in _store_rules(snap)
        if r.get("groupId") == group_id and r.get("id")
    ]
    member_set = set(grouped_rules)
    if not member_set:
        raise OracleFailure("group_empty", f"no stored rules carry groupId {group_id!r}")
    group_policies = [
        p for p in _policies_list(snap) if set(p.get("ruleIds") or []) == member_set
    ]
    if len(group_policies) != 1:
        raise OracleFailure(
            "group_policy_count",
            f"expected exactly one policy with ruleIds == group members "
            f"{sorted(member_set)}, got {len(group_policies)}",
        )
    # No additional policy may reference a single member on its own.
    for p in _policies_list(snap):
        rids = set(p.get("ruleIds") or [])
        if rids == member_set:
            continue
        overlap = rids & member_set
        if overlap:
            raise OracleFailure(
                "double_representation",
                f"policy {p.get('id')!r} additionally references group members {sorted(overlap)}",
            )


def assert_from_plan_triple(snap: PersistedSnapshot, save_result: SaveResult) -> None:
    """producer sidecar + gate rule + Policy record, all binding ids cross-match."""
    policy_id = save_result.policy_id
    producer_id = save_result.producer_id
    gate_id = save_result.gate_id
    if not (policy_id and producer_id and gate_id):
        raise OracleFailure(
            "from_plan_incomplete",
            f"save result missing ids: policy={policy_id} producer={producer_id} gate={gate_id}",
        )
    if policy_id != producer_id:
        raise OracleFailure(
            "policy_id_not_producer",
            f"policyId {policy_id!r} must equal producerId {producer_id!r}",
        )
    # Gate present in custom_rules.
    gate = _find_rule(snap, gate_id)
    if gate is None:
        raise OracleFailure("gate_missing", f"gate rule {gate_id!r} not in store")
    # Policy present with the right shape.
    policies = snap.store.get("policies", {})
    policy = policies.get(policy_id) if isinstance(policies, dict) else None
    if not isinstance(policy, dict):
        raise OracleFailure("policy_missing", f"policy {policy_id!r} not in store")
    if (policy.get("ruleIds") or []) != [gate_id]:
        raise OracleFailure(
            "policy_ruleids",
            f"policy ruleIds {policy.get('ruleIds')} != [{gate_id!r}]",
        )
    binding = policy.get("binding") or {}
    if binding.get("producerRuleId") != producer_id:
        raise OracleFailure("binding_producer", "binding.producerRuleId != producerId")
    if binding.get("gateRuleId") != gate_id:
        raise OracleFailure("binding_gate", "binding.gateRuleId != gateId")
    etype = binding.get("evidenceType") or ""
    if not etype.startswith("custom:"):
        raise OracleFailure("evidence_type_prefix", f"evidenceType {etype!r} not 'custom:'-prefixed")
    require = (
        gate.get("what", {}).get("payload", {}).get("requireEvidence", {})
        if isinstance(gate, dict)
        else {}
    )
    if require.get("producerRuleId") != producer_id:
        raise OracleFailure(
            "gate_evidence_producer",
            "gate.what.payload.requireEvidence.producerRuleId != producerId",
        )
    review = policy.get("review") or {}
    if review.get("verdict") != "unreviewed":
        raise OracleFailure(
            "review_verdict",
            f"policy review.verdict {review.get('verdict')!r} != 'unreviewed'",
        )
    # Producer present in the dashboard sidecar with id == producerId.
    from magi_agent.customize.policy_persist import _writable_dashboard_pack_root
    from magi_agent.packs.dashboard_authored import read_sidecar

    producers = read_sidecar(_writable_dashboard_pack_root())
    prod_ids = {c.model_dump(by_alias=True).get("id") for c in producers}
    if producer_id not in prod_ids:
        raise OracleFailure(
            "producer_missing",
            f"producer {producer_id!r} not in sidecar ({sorted(prod_ids)})",
        )


def assert_promotion_idempotent(
    snap_before: PersistedSnapshot, snap_after: PersistedSnapshot
) -> None:
    """Re-PUT of an existing rule id (an UPDATE) creates zero new policies."""
    before_ids = {p.get("id") for p in _policies_list(snap_before)}
    after_ids = {p.get("id") for p in _policies_list(snap_after)}
    new_ids = after_ids - before_ids
    if new_ids:
        raise OracleFailure(
            "promotion_not_idempotent",
            f"re-PUT created new policies: {sorted(new_ids)}",
        )


def assert_reserved_id_rejected(client: Any) -> None:
    """PUT of a first-party reserved id returns 409 builtin_id_reserved and the
    builtin card is unchanged."""
    for reserved in _RESERVED_IDS:
        resp = client.put(f"/v1/app/policies/{reserved}", json={"displayName": "x", "ruleIds": []})
        if resp.status_code != 409:
            raise OracleFailure(
                "reserved_not_409",
                f"PUT /policies/{reserved} returned {resp.status_code}, expected 409",
            )
        body = resp.json()
        if body.get("error") != "builtin_id_reserved":
            raise OracleFailure(
                "reserved_wrong_error",
                f"PUT /policies/{reserved} error {body.get('error')!r} != 'builtin_id_reserved'",
            )


def assert_catalog_consistent(snap: PersistedSnapshot) -> None:
    """Every catalog policy entry has a valid enabledState + source; source_citation
    is present with a gateMode; every user policy appears exactly once."""
    catalog = _catalog_policies(snap)
    if not catalog:
        raise OracleFailure("catalog_empty", "catalog has no policy entries")
    saw_source_citation = False
    catalog_user_ids: list[str] = []
    for entry in catalog:
        pid = entry.get("id")
        state = entry.get("enabledState")
        source = entry.get("source")
        if state not in _VALID_ENABLED_STATES:
            raise OracleFailure(
                "bad_enabled_state",
                f"policy {pid!r} enabledState {state!r} not in {sorted(_VALID_ENABLED_STATES)}",
            )
        if source not in _VALID_SOURCES:
            raise OracleFailure(
                "bad_source",
                f"policy {pid!r} source {source!r} not in {sorted(_VALID_SOURCES)}",
            )
        if pid == "source_citation":
            saw_source_citation = True
            if source != "builtinPolicy":
                raise OracleFailure("citation_not_builtin", "source_citation source != builtinPolicy")
            if entry.get("gateMode") is None:
                raise OracleFailure("citation_no_gatemode", "source_citation missing gateMode")
        if entry.get("origin") == "user":
            catalog_user_ids.append(pid)
    if not saw_source_citation:
        raise OracleFailure("citation_absent", "source_citation not in catalog")
    # Every USER policy in the store appears in the catalog exactly once.
    store_user_ids = [p.get("id") for p in _user_policies(snap)]
    for uid in store_user_ids:
        count = catalog_user_ids.count(uid)
        if count != 1:
            raise OracleFailure(
                "catalog_user_count",
                f"user policy {uid!r} appears {count}x in catalog (expected 1)",
            )


def assert_store_untouched(
    snap_before: PersistedSnapshot, snap_after: PersistedSnapshot
) -> None:
    """The customize.json bytes are unchanged (I8 / never_persists scenarios)."""
    if snap_before.store_hash != snap_after.store_hash:
        raise OracleFailure(
            "store_touched",
            f"store hash changed: {snap_before.store_hash[:12]} -> {snap_after.store_hash[:12]}",
        )
