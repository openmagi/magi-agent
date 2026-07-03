"""Mode ``scoped_policy_ids`` → per-turn policy overlay (resolver only; inert).

A mode's ``scoped_policy_ids`` activate user-authored policies **only while that
mode is active**. This module resolves those ids into a bucketed overlay. The
pre-final gate consumes it via :func:`scoped_prefinal_validator_refs` (PR-D2);
the tool-time gate + dashboard producer force-include land in PR-D3. Everything
is gated by the same flags as the enabled-rule passes and is empty when no mode
is active, so an inactive-mode / flags-off runtime is byte-identical to today.

Namespace (the dashboard's prefixed unified ids; ``_POLICY_RE`` in
``customize/modes.py`` already permits ``:``):

- ``custom_rule:<id>`` — a ``verification.custom_rules[]`` rule. v1 resolves the
  two kinds that have a clean per-turn read-point:
  - ``deterministic_ref`` → its ``what.payload.ref`` joins the pre-final
    validator set (re-checked with ``is_known_ref``).
  - ``tool_perm`` → its rule id joins the tool-time perm set.
- ``dashboard_check:<id>`` — a dashboard check id joins the dashboard producer's
  active set.

**Deferred** (recorded as dropped, not applied): ``seam_spec:<id>`` and bare
``verifier:`` refs — the seam-spec runtime apply path is dormant, so those do
not resolve to a live ref today. Hard-safety rejection (dropping a global-only
hard policy from a mode's scope) only becomes relevant once those resolve;
``custom_rules`` and dashboard checks carry no hard flag, so v1 never needs it.

The resolver is a pure function: the caller supplies the lookup sources
(``custom_rules`` from ``customize_verification_policy``, the set of known
``dashboard_check`` ids from ``read_sidecar``). Force-include semantics: a scoped
id is added **regardless of the rule's own ``enabled`` flag** — the whole point
is to activate a policy that is otherwise off for this mode's turns. Force-include
overrides the rule's ``enabled`` flag but NOT a producer's config gate: a
``deterministic_ref`` whose evidence producer is disabled by env still drops as
``unknown_ref`` (``is_known_ref`` returns False), since a ref whose producer
emits nothing cannot be required.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from magi_agent.customize.what_menu import is_known_ref

__all__ = [
    "ScopedPolicyOverlay",
    "active_scoped_policy_ids",
    "resolve_scoped_policy_overlay",
    "scoped_policies_ruleids",
    "scoped_prefinal_validator_refs",
]

# Namespaces that resolve to a live runtime ref in v1.
_NS_CUSTOM_RULE = "custom_rule"
_NS_DASHBOARD_CHECK = "dashboard_check"
# A ``policy:<id>`` ref fans out to its member rule refs (each resolved exactly
# as a ``custom_rule:<memberId>`` would be), unioned idempotently into the same
# buckets. A policy is a named composition of 1..N rules; "Mode contains Policy
# or Rule" is thus a ref-grammar extension, not a new read-point.
_NS_POLICY = "policy"
# Namespaces recognized but intentionally not applied yet (dormant runtime).
_DEFERRED_NAMESPACES = frozenset({"seam_spec", "verifier"})


@dataclass(frozen=True)
class ScopedPolicyOverlay:
    """The per-turn additions a mode's scoped policies contribute, bucketed by
    the read-point that must consume each. ``dropped`` records ``(scoped_id,
    reason)`` for ids that did not activate (unknown / unsupported / deferred /
    malformed) so a stale mode is visible rather than silently ineffective."""

    prefinal_validator_refs: tuple[str, ...] = ()
    tool_perm_rule_ids: tuple[str, ...] = ()
    dashboard_check_ids: tuple[str, ...] = ()
    dropped: tuple[tuple[str, str], ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (
            self.prefinal_validator_refs
            or self.tool_perm_rule_ids
            or self.dashboard_check_ids
        )


def _parse(scoped_id: str) -> tuple[str, str] | None:
    """Split ``namespace:local`` once. Returns ``None`` for a missing/empty
    namespace or local part."""
    namespace, sep, local = scoped_id.partition(":")
    if not sep or not namespace or not local:
        return None
    return namespace, local


def _dedupe(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _route_custom_rule(
    local: str,
    drop_ref: str,
    rules_by_id: Mapping[str, Mapping[str, object]],
    *,
    prefinal: list[str],
    tool_perm: list[str],
    dropped: list[tuple[str, str]],
    reason_prefix: str = "",
) -> None:
    """Route one custom-rule id into the overlay buckets by its ``what.kind``.

    Shared by the ``custom_rule:`` branch and the ``policy:`` fan-out (each
    policy member is routed exactly as the equivalent ``custom_rule:`` ref).
    Drops are recorded against ``drop_ref`` (the top-level scoped id) so a stale
    mode ref stays visible; ``reason_prefix`` attributes a policy member's drop
    to its member id (e.g. ``member[cr_x]:unknown``)."""
    rule = rules_by_id.get(local)
    if rule is None:
        dropped.append((drop_ref, f"{reason_prefix}unknown"))
        return
    what = rule.get("what")
    what = what if isinstance(what, Mapping) else {}
    kind = what.get("kind")
    if kind == "deterministic_ref":
        payload = what.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        ref = payload.get("ref")
        if isinstance(ref, str) and is_known_ref(ref):
            prefinal.append(ref)
        else:
            dropped.append((drop_ref, f"{reason_prefix}unknown_ref"))
    elif kind == "tool_perm":
        tool_perm.append(local)
    else:
        dropped.append((drop_ref, f"{reason_prefix}unsupported_kind:{kind}"))


def resolve_scoped_policy_overlay(
    scoped_policy_ids: Sequence[str],
    *,
    custom_rules: Sequence[Mapping[str, object]],
    dashboard_check_ids: Collection[str],
    policies: Mapping[str, Sequence[str]] | None = None,
) -> ScopedPolicyOverlay:
    """Resolve a mode's ``scoped_policy_ids`` into a bucketed overlay.

    Pure + fail-soft: an id that resolves to nothing is recorded in ``dropped``,
    never raised. A rule's own ``enabled`` flag is intentionally ignored (scoping
    force-activates it for the turn).

    ``policies`` maps a policy id (bare slug) to its member rule ids; a
    ``policy:<id>`` ref fans out to those members (each routed as a
    ``custom_rule:<memberId>``), unioned idempotently into the buckets (so a
    member also referenced bare, or shared across two policies, is not
    double-counted). ``None`` (the default) makes ``policy:`` refs unresolvable
    (recorded as dropped), preserving the pre-fan-out behavior for callers that
    do not pass it.
    """
    prefinal: list[str] = []
    tool_perm: list[str] = []
    dashboard: list[str] = []
    dropped: list[tuple[str, str]] = []

    rules_by_id: dict[str, Mapping[str, object]] = {}
    for rule in custom_rules:
        if isinstance(rule, Mapping):
            rid = rule.get("id")
            if isinstance(rid, str) and rid and rid not in rules_by_id:
                rules_by_id[rid] = rule

    policies_map = policies or {}
    known_checks = set(dashboard_check_ids)
    seen: set[str] = set()
    for raw in scoped_policy_ids:
        if not isinstance(raw, str):
            dropped.append((str(raw), "malformed"))
            continue
        if raw in seen:
            continue
        seen.add(raw)
        parsed = _parse(raw)
        if parsed is None:
            dropped.append((raw, "malformed"))
            continue
        namespace, local = parsed

        if namespace == _NS_CUSTOM_RULE:
            _route_custom_rule(
                local, raw, rules_by_id,
                prefinal=prefinal, tool_perm=tool_perm, dropped=dropped,
            )
        elif namespace == _NS_POLICY:
            member_ids = policies_map.get(local)
            if not member_ids:
                dropped.append((raw, "unknown"))
                continue
            for member_id in member_ids:
                if not isinstance(member_id, str) or not member_id:
                    continue
                _route_custom_rule(
                    member_id, raw, rules_by_id,
                    prefinal=prefinal, tool_perm=tool_perm, dropped=dropped,
                    reason_prefix=f"member[{member_id}]:",
                )
        elif namespace == _NS_DASHBOARD_CHECK:
            if local in known_checks:
                dashboard.append(local)
            else:
                dropped.append((raw, "unknown"))
        elif namespace in _DEFERRED_NAMESPACES:
            dropped.append((raw, "deferred"))
        else:
            dropped.append((raw, "unsupported_namespace"))

    return ScopedPolicyOverlay(
        prefinal_validator_refs=_dedupe(prefinal),
        tool_perm_rule_ids=_dedupe(tool_perm),
        dashboard_check_ids=_dedupe(dashboard),
        dropped=tuple(dropped),
    )


def scoped_policies_ruleids(path: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Map each stored policy's id -> its member rule ids, for ``policy:`` ref
    fan-out in :func:`resolve_scoped_policy_overlay`. Fail-soft: returns ``{}``
    on any error so a broken policies store never wedges the resolver."""
    try:
        from magi_agent.customize.policies import list_policies  # noqa: PLC0415

        return {p.policy_id: p.rule_ids for p in list_policies(path)}
    except Exception:
        return {}


def active_scoped_policy_ids() -> tuple[str, ...]:
    """The active mode's ``scoped_policy_ids`` for this turn, resolved like the
    tool-delta seams (per-turn selection wins over the sticky default). Empty on
    no mode / unknown mode / any error (fail-soft ⇒ byte-identical). Works on
    both the CLI and serve paths because it reads the same lazy sources."""
    try:
        from magi_agent.customize.modes import active_mode_id, get_mode
        from magi_agent.runtime.per_turn_agent_mode_context import (
            current_per_turn_agent_mode,
        )

        mode_id = current_per_turn_agent_mode() or active_mode_id()
        if not mode_id:
            return ()
        mode = get_mode(mode_id)
        if mode is None:
            return ()
        return tuple(mode.scoped_policy_ids)
    except Exception:
        return ()


def scoped_prefinal_validator_refs() -> tuple[str, ...]:
    """The active mode's scoped deterministic-ref validator refs for this turn.

    PR-D2 consumption bridge for the pre-final gate: loads the customize policy
    (the source of ``custom_rules``), resolves the active mode's overlay, and
    returns its ``prefinal_validator_refs`` so the caller can union them into the
    pre-final ``required_validators``.

    Gated by the SAME flags as the enabled-deterministic-ref pass
    (``_apply_customize_verification``): ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED``
    AND ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED``. Force-include never bypasses an
    operator flag, so this returns ``()`` (byte-identical) when either is off, on
    any error, or when no mode is active. Dashboard checks are consumed
    separately (PR-D3), so an empty dashboard-check set is passed here.
    """
    try:
        from magi_agent.config.flags import flag_profile_bool  # noqa: PLC0415

        if not (
            flag_profile_bool("MAGI_CUSTOMIZE_VERIFICATION_ENABLED")
            and flag_profile_bool("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED")
        ):
            return ()
        # Early-out before loading the customize policy: when no mode is active
        # (the common case) there is nothing to force-include, so we avoid a
        # per-turn overrides read on the hot pre-final path.
        scoped_ids = active_scoped_policy_ids()
        if not scoped_ids:
            return ()
        from magi_agent.customize.store import load_overrides  # noqa: PLC0415
        from magi_agent.customize.verification_policy import (  # noqa: PLC0415
            CustomizeVerificationPolicy,
        )

        policy = CustomizeVerificationPolicy.from_overrides(load_overrides())
        overlay = resolve_scoped_policy_overlay(
            scoped_ids,
            custom_rules=policy.custom_rules,
            dashboard_check_ids=(),
            policies=scoped_policies_ruleids(),
        )
        return overlay.prefinal_validator_refs
    except Exception:
        return ()
