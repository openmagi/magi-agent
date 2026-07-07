"""POLICIES: named user-intent units, typed model + customize.json CRUD.

A *policy* is a named user-intent unit composed of 1..N custom *rules*. The rule
(``custom_rule``, 12 kinds) is the atomic executable control unit; the policy is
the authoring / grouping / binding unit that names a user-meaning-level intent
("require a verified source before a high-risk tool") which is often several
rules working together (a producer that records evidence + a gate that consumes
it).

A policy carries NO precedence of its own. Custom rules cannot be "hard," so a
policy-level "hard if any member is hard" would be dead logic, and a policy
mixing precedence tiers in one mode is incoherent. hard/soft stays a per-rule
property; ALL custom_rule-composed policies are soft; activation happens per
member rule by that rule's own precedence.

A policy is referenced from a mode's ``scopedPolicyIds`` as ``policy:<id>``
(the ``policy:`` prefix mirrors the existing ``custom_rule:`` / ``dashboard_check:``
ref convention); the stored key/``id`` is the bare slug. The resolver ref
fan-out that expands a ``policy:`` ref into its member rule refs is a separate
slice (it lives in the scoped_policy resolver, not here).

Storage only in this module: NO runtime consumption. See clawy
docs/plans/2026-07-03-policy-abstraction-and-organic-multi-rule-authoring-design.md.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from magi_agent.customize.store import load_overrides, save_overrides

# Bare-slug policy id (the mode ref adds the ``policy:`` prefix). Same shape as
# the mode-id token so the two id spaces read consistently.
_POLICY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# A member rule id: reference-safe token with NO ``:`` (custom rule ids never
# carry the ``custom_rule:`` prefix separator; see custom_rules id validation).
_RULE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
# An operator-named evidence type on a binding (``custom:PascalCase`` or a
# builtin type name); kept permissive here (the emitter enforces the exact
# ``validate_evidence_type_name`` contract).
_EVIDENCE_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9:._-]{0,127}$")

_MAX_POLICIES = 256
_MAX_RULE_IDS = 64
_MAX_INTENT = 2_000
_MAX_ISSUES = 32
_MAX_ISSUE_CHARS = 500

# Verdicts a policy-integrity review can carry, plus the migration sentinel.
_VALID_VERDICTS = frozenset(
    {"unreviewed", "aligned", "mismatch", "overbroad", "underbroad", "unknown"}
)

_MODEL_CONFIG = ConfigDict(
    frozen=True, populate_by_name=True, extra="forbid", validate_default=True
)


def _dedupe_valid_rule_ids(value: tuple[str, ...]) -> tuple[str, ...]:
    if len(value) > _MAX_RULE_IDS:
        raise ValueError(f"ruleIds: too many entries (max {_MAX_RULE_IDS})")
    seen: list[str] = []
    for item in value:
        if _RULE_ID_RE.fullmatch(item) is None:
            raise ValueError(f"ruleIds: invalid entry {item!r}")
        if item not in seen:
            seen.append(item)
    return tuple(seen)


class PolicyBinding(BaseModel):
    """The producer<->gate identity binding for a security policy (§3.3.3).

    The unlock join is by producer IDENTITY, not evidence-type name: the gate
    accepts only records emitted by ``producerRuleId``. Optional; only
    security-shaped policies carry it. Storage-shape only in this phase.
    """

    model_config = _MODEL_CONFIG

    producer_rule_id: str = Field(alias="producerRuleId")
    gate_rule_id: str = Field(alias="gateRuleId")
    evidence_type: str = Field(alias="evidenceType")

    @field_validator("producer_rule_id", "gate_rule_id")
    @classmethod
    def _validate_rule_id(cls, value: str) -> str:
        if _RULE_ID_RE.fullmatch(value) is None:
            raise ValueError("binding rule id must be a reference-safe token (no ':')")
        return value

    @field_validator("evidence_type")
    @classmethod
    def _validate_evidence_type(cls, value: str) -> str:
        if _EVIDENCE_TYPE_RE.fullmatch(value) is None:
            raise ValueError("binding evidenceType must be a valid evidence type name")
        return value


class PolicyReview(BaseModel):
    """Cached policy-integrity verdict (§6). ``memberHash`` is a hash of the
    member rule bodies + ruleIds; a mismatch marks the verdict stale so
    activation never trusts a stale pass (staleness gates authoring/attach
    flows only; an already-attached policy keeps enforcing with the stale
    verdict surfaced)."""

    model_config = _MODEL_CONFIG

    verdict: str = "unreviewed"
    checked_at: str = Field(default="", alias="checkedAt")
    issues: tuple[str, ...] = ()
    member_hash: str = Field(default="", alias="memberHash")

    @field_validator("verdict")
    @classmethod
    def _validate_verdict(cls, value: str) -> str:
        if value not in _VALID_VERDICTS:
            raise ValueError(f"verdict must be one of {sorted(_VALID_VERDICTS)}")
        return value

    @field_validator("issues")
    @classmethod
    def _validate_issues(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > _MAX_ISSUES:
            value = value[:_MAX_ISSUES]
        return tuple(str(i)[:_MAX_ISSUE_CHARS] for i in value)


class Policy(BaseModel):
    model_config = _MODEL_CONFIG

    policy_id: str = Field(alias="id")
    display_name: str = Field(alias="displayName")
    intent: str = ""
    rule_ids: tuple[str, ...] = Field(default=(), alias="ruleIds")
    binding: PolicyBinding | None = None
    origin: Literal["user", "builtin"] = "user"
    review: PolicyReview | None = None
    # Whether a user may turn this policy OFF from Customize. User policies are
    # always disableable. A first-party builtin is a strong DEFAULT, not an
    # un-removable floor -- EXCEPT one whose gate can block (a safety floor),
    # which sets this False. This is the truthful display SIGNAL a surface can
    # read to render a floor as locked; it does NOT itself gate anything. The
    # actual opt-out mechanism (env projection) is enforced by catalog
    # membership in ``builtin_policy_overrides`` (a floor is simply absent from
    # the catalog), so the two are consistent by construction.
    user_disableable: bool = Field(default=True, alias="userDisableable")

    @field_validator("policy_id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if _POLICY_ID_RE.fullmatch(value) is None:
            raise ValueError(
                "policy id must be a lowercase safe token [a-z0-9][a-z0-9_-]*"
            )
        return value

    @field_validator("display_name")
    @classmethod
    def _validate_display(cls, value: str) -> str:
        # Drop control / non-printable chars (defense-in-depth: policy names are
        # a UI rendering + spoofing surface; do not rely on frontend escaping).
        text = "".join(ch for ch in value if ch.isprintable()).strip()
        if not text:
            raise ValueError("policy displayName must be non-empty")
        return text[:120]

    @field_validator("intent")
    @classmethod
    def _cap_intent(cls, value: str) -> str:
        return value[:_MAX_INTENT]

    @field_validator("rule_ids")
    @classmethod
    def _validate_rule_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _dedupe_valid_rule_ids(value)

    def to_payload(self) -> dict:
        return self.model_dump(by_alias=True, mode="json", exclude_none=True)


# ---------------------------------------------------------------------------
# First-party (builtin) policies
# ---------------------------------------------------------------------------
#
# A builtin policy gives a runtime-native behavior a policy-visible identity so
# it appears in the Rules/Policies surface, is mode-scopable via
# ``scoped_policy_ids`` (``policy:<id>``), and its verdicts land in Audit (the
# ``lifecycle_audit`` precedent). Builtins are read-only (``upsert_policy``
# rejects ``origin == "builtin"``); a user clones to a new id to customize.

# The source-citation policy: the Wave 1-4 citation feature expressed as one
# first-party policy composed of four member rules (design Section 10). Capture
# and render are deterministic runtime code paths given policy-visible member
# ids; the gate is this wave's deterministic repair-then-audit path; the
# claim_coverage member is the default-OFF MAGI_VERIFY_CLAIM_CITATION judge
# rehomed as a staged advisory member (design 11.4). The PolicyBinding gives
# ``source_citation.capture`` (which emits ``SourceInspection`` producer_control
# records) a producer identity, so a future user policy can require citation
# evidence via the existing identity-join without new machinery.
_SOURCE_CITATION_POLICY = Policy(
    id="source_citation",
    displayName="Source Citation",
    intent=(
        "Capture every external read as a citable source, render inline "
        "citations, and gate high-risk claims (figures, dates, quotes, named "
        "superlatives) so they are attributed to a registered source. "
        "Repair-then-fail-open; never blocks the turn permanently."
    ),
    ruleIds=(
        "source_citation.capture",
        "source_citation.render",
        "source_citation.gate",
        "source_citation.claim_coverage",
    ),
    binding=PolicyBinding(
        producerRuleId="source_citation.capture",
        gateRuleId="source_citation.gate",
        evidenceType="SourceInspection",
    ),
    origin="builtin",
    # Floor: the gate can BLOCK (repair mode), so it is not user-disableable.
    # Correspondingly absent from ``builtin_policy_overrides.BUILTIN_POLICY_TOGGLES``.
    userDisableable=False,
)


# The verify-before-replying policy: the pre-final nudge feature expressed as
# one first-party policy composed of five member rules (design Section 11).
# The evidence-bound members (claim_citation, evidence_consistency,
# activity_grounding) emit high-confidence findings; the heuristic and LLM-skeptic
# members (sycophancy_heuristics, skeptic_review) emit advisory findings.
# No PolicyBinding: verify findings are audit records, not unlock evidence, and
# must never satisfy an evidence gate (design Section 11 note 3).
_VERIFY_BEFORE_REPLYING_POLICY = Policy(
    id="verify_before_replying",
    displayName="Verify Before Replying",
    intent=(
        "At the pre-final boundary, audit the candidate reply against this "
        "turn's tool activity and evidence ledger, then hand specific findings "
        "back to the model so it can autonomously decide to ship as-is, revise, "
        "or loop back into more work. Nothing is blocked; the model decides. "
        "High-confidence findings are evidence-grounded (missing citations, "
        "ledger contradictions, ungrounded action claims); advisory findings "
        "flag sycophancy and overconfidence heuristics. All findings are durable "
        "and observable, with a per-finding resolution status that makes the "
        "ignore-rate measurable -- the core quality signal of this policy."
    ),
    ruleIds=(
        "verify_before_replying.claim_citation",
        "verify_before_replying.evidence_consistency",
        "verify_before_replying.activity_grounding",
        "verify_before_replying.sycophancy_heuristics",
        "verify_before_replying.skeptic_review",
    ),
    origin="builtin",
    # Non-blocking nudge: safe to opt out of. Listed in
    # ``builtin_policy_overrides.BUILTIN_POLICY_TOGGLES``.
    userDisableable=True,
)

BUILTIN_POLICIES: tuple[Policy, ...] = (
    _SOURCE_CITATION_POLICY,
    _VERIFY_BEFORE_REPLYING_POLICY,
)


def builtin_policies() -> tuple[Policy, ...]:
    """The first-party read-only policies always present in the surface."""
    return BUILTIN_POLICIES


# ---------------------------------------------------------------------------
# Store access
# ---------------------------------------------------------------------------


def _policies_raw(path: Path | None) -> dict:
    raw = load_overrides(path).get("policies", {})
    return raw if isinstance(raw, dict) else {}


def _custom_rules_raw(path: Path | None) -> list:
    verification = load_overrides(path).get("verification", {})
    rules = verification.get("custom_rules", []) if isinstance(verification, dict) else []
    return rules if isinstance(rules, list) else []


def list_policies(path: Path | None = None) -> tuple[Policy, ...]:
    """All valid policies (first-party builtins + stored), sorted by id.

    Builtins are always present so the Rules/Policies surface tells the truth
    about the runtime-native policies that run (e.g. ``source_citation``). A
    stored policy that reuses a builtin id shadows it (a user clone wins). Stored
    malformed entries are skipped; a stored dict key must match its payload id
    (hand-edit guard)."""
    out: list[Policy] = []
    seen_ids: set[str] = set()
    for key, raw in _policies_raw(path).items():
        if not isinstance(raw, dict):
            continue
        try:
            policy = Policy.model_validate(raw)
        except ValidationError:
            continue
        if policy.policy_id != key:
            continue
        out.append(policy)
        seen_ids.add(policy.policy_id)
    for builtin in BUILTIN_POLICIES:
        if builtin.policy_id not in seen_ids:
            out.append(builtin)
    return tuple(sorted(out, key=lambda p: p.policy_id))


def get_policy(policy_id: str, path: Path | None = None) -> Policy | None:
    raw = _policies_raw(path).get(policy_id)
    if isinstance(raw, dict):
        try:
            policy = Policy.model_validate(raw)
            if policy.policy_id == policy_id:
                return policy
        except ValidationError:
            pass
    for builtin in BUILTIN_POLICIES:
        if builtin.policy_id == policy_id:
            return builtin
    return None


def upsert_policy(policy: Policy, path: Path | None = None) -> None:
    if policy.origin == "builtin":
        raise ValueError("built-in policies are read-only; clone to a new id to customize")
    overrides = load_overrides(path)
    policies = dict(
        overrides.get("policies", {}) if isinstance(overrides.get("policies"), dict) else {}
    )
    if policy.policy_id not in policies and len(policies) >= _MAX_POLICIES:
        raise ValueError(f"too many policies (max {_MAX_POLICIES})")
    policies[policy.policy_id] = policy.to_payload()
    overrides["policies"] = policies
    save_overrides(overrides, path)


def delete_policy(policy_id: str, path: Path | None = None) -> None:
    overrides = load_overrides(path)
    policies = dict(
        overrides.get("policies", {}) if isinstance(overrides.get("policies"), dict) else {}
    )
    if policy_id not in policies:
        return
    del policies[policy_id]
    overrides["policies"] = policies
    save_overrides(overrides, path)


# ---------------------------------------------------------------------------
# Implicit 1-rule policies (read-time convenience)
# ---------------------------------------------------------------------------


def implicit_policy_for_rule(rule: dict) -> Policy | None:
    """A non-persisted 1-rule Policy VIEW for a bare (ungrouped) custom rule.

    Read-time convenience only: an implicit policy has no persisted id/intent/
    review and cannot be reviewed or referenced by a mode until PROMOTED to a
    persisted Policy. Returns ``None`` for a rule with no usable id.
    """
    rule_id = rule.get("id") if isinstance(rule, dict) else None
    if not isinstance(rule_id, str) or _RULE_ID_RE.fullmatch(rule_id) is None:
        return None
    label = rule_id
    try:
        return Policy(
            id=_slugify(rule_id),
            displayName=label,
            ruleIds=(rule_id,),
        )
    except ValidationError:
        return None


# ---------------------------------------------------------------------------
# groupId -> Policy migration (1:1)
# ---------------------------------------------------------------------------


def _slugify(value: str) -> str:
    """Best-effort bare-slug from an arbitrary string (groupId / rule id)."""
    lowered = "".join(ch if (ch.isalnum() or ch in "_-") else "-" for ch in value.lower())
    trimmed = lowered.strip("-_") or "policy"
    # Ensure it starts with an alnum (the id regex requires it).
    if not trimmed[0].isalnum():
        trimmed = f"p{trimmed}"
    return trimmed[:64]


def migrate_groups_to_policies(path: Path | None = None) -> int:
    """Promote each distinct ``groupId`` on the stored custom rules to a Policy
    (1:1), idempotently. Returns the number of policies created.

    Migrated policies carry a synthesized displayName, a placeholder (empty)
    intent, and ``review.verdict = "unreviewed"`` (do NOT run review at
    migration). Ungrouped rules are left as implicit 1-rule policies (no
    persisted change). A group whose slug already maps to an existing policy is
    skipped (idempotent)."""
    groups: dict[str, list[str]] = {}
    for rule in _custom_rules_raw(path):
        if not isinstance(rule, dict):
            continue
        group_id = rule.get("groupId")
        rule_id = rule.get("id")
        if not (isinstance(group_id, str) and group_id.strip()):
            continue
        if not (isinstance(rule_id, str) and _RULE_ID_RE.fullmatch(rule_id)):
            continue
        groups.setdefault(group_id, [])
        if rule_id not in groups[group_id]:
            groups[group_id].append(rule_id)

    created = 0
    for group_id, rule_ids in groups.items():
        slug = _slugify(group_id)
        if get_policy(slug, path) is not None:
            continue  # idempotent: already migrated
        try:
            policy = Policy(
                id=slug,
                displayName=group_id[:120],
                intent="",
                ruleIds=tuple(rule_ids),
                review=PolicyReview(verdict="unreviewed"),
            )
        except ValidationError:
            continue
        upsert_policy(policy, path)
        created += 1
    return created
