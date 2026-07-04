"""Persist an assembled policy plan across its three stores.

A compiled policy plan (see ``policy_compiler`` / ``nl_policy_interactive``) has
parts in three stores: the PRODUCER is a dashboard-pack check
(``dashboard-checks.json`` sidecar), the GATE is a ``custom_rule``
(``customize.json`` ``verification.custom_rules``), and the POLICY record ties
them together (``customize.json`` ``policies``) via the identity binding. This
module upserts all three idempotently, gating on the plan being structurally
sound first (never persist a dangling/mis-bound policy).

customize -> packs is an established edge (the compilers already import
``packs.dashboard_authored``); the writable-pack-root resolution mirrors the
transport dashboard route (never write into the read-only bundled base).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from magi_agent.customize.custom_rules import validate_custom_rule
from magi_agent.customize.policies import Policy, upsert_policy
from magi_agent.customize.policy_plan import validate_policy_plan
from magi_agent.customize.store import set_custom_rule
from magi_agent.packs.dashboard_authored import (
    DASHBOARD_PACK_DIR_NAME,
    DashboardCheck,
    read_sidecar,
    validate_dashboard_check,
    write_pack,
)


class PolicyPersistError(ValueError):
    """The plan is not structurally sound, or a part failed to validate."""


def _writable_dashboard_pack_root() -> Path:
    """The writable dashboard pack dir (never the read-only bundled base).

    Mirrors ``transport.packs_dashboard._pack_root`` but stays in the packs
    layer (no transport import): pick the first search base that is not the
    bundled first-party base (normally ``~/.magi/packs``)."""
    from magi_agent.packs.discovery import (  # noqa: PLC0415
        _bundled_firstparty_base,
        default_search_bases,
    )

    try:
        bundled_resolved = _bundled_firstparty_base().resolve()
    except OSError:
        bundled_resolved = _bundled_firstparty_base()
    for base in default_search_bases():
        base = Path(base)
        try:
            resolved = base.resolve()
        except OSError:
            resolved = base
        if resolved != bundled_resolved:
            return base / DASHBOARD_PACK_DIR_NAME
    return Path.home() / ".magi" / "packs" / DASHBOARD_PACK_DIR_NAME


def _policy_display_name(plan: dict[str, Any]) -> str:
    intent = str(plan.get("intent") or "").strip()
    if intent:
        return intent[:120]
    etype = plan.get("binding", {}).get("evidenceType", "evidence")
    gated = plan.get("gate", {}).get("what", {}).get("payload", {}).get("match", {}).get("tool", "a tool")
    return f"Require {etype} before {gated}"[:120]


def persist_policy_plan(plan: Any, *, path: Path | None = None) -> dict[str, str]:
    """Persist a compiled plan (producer + gate + Policy). Idempotent upsert
    by id. Returns ``{policyId, producerId, gateId}``.

    Raises :class:`PolicyPersistError` when the plan is structurally unsound or
    a part fails its own validator, BEFORE writing anything (no partial writes
    from a bad plan; per-store writes are individually atomic).
    """
    if not isinstance(plan, dict):
        raise PolicyPersistError("plan must be an object")
    producer = plan.get("producer")
    gate = plan.get("gate")
    binding = plan.get("binding")
    if not (isinstance(producer, dict) and isinstance(gate, dict) and isinstance(binding, dict)):
        raise PolicyPersistError("plan requires producer + gate + binding to persist")

    findings = validate_policy_plan(plan)
    findings += [f"producer: {e}" for e in validate_dashboard_check(producer)]
    findings += [f"gate: {e}" for e in validate_custom_rule(gate)]
    if findings:
        raise PolicyPersistError("; ".join(findings))

    producer_id = str(producer["id"])
    gate_id = str(gate["id"])

    # Build the Policy + validate its shape BEFORE any write, so a bad
    # policy id / binding fails before we touch a store.
    try:
        policy = Policy.model_validate(
            {
                "id": producer_id,
                "displayName": _policy_display_name(plan),
                "intent": str(plan.get("intent") or ""),
                "ruleIds": [gate_id],
                "binding": binding,
                "review": {"verdict": "unreviewed"},
            }
        )
    except Exception as exc:  # noqa: BLE001
        raise PolicyPersistError(f"policy record invalid: {exc}") from exc

    # 1. Producer -> dashboard sidecar (upsert by id).
    root = _writable_dashboard_pack_root()
    check = DashboardCheck.model_validate(producer)
    checks = [c for c in read_sidecar(root) if c.id != check.id]
    checks.append(check)
    write_pack(root, checks)

    # 2. Gate -> custom_rules (upsert by id).
    set_custom_rule(gate, path=path)

    # 3. Policy record -> policies (upsert by id).
    upsert_policy(policy, path=path)

    return {"policyId": policy.policy_id, "producerId": producer_id, "gateId": gate_id}
