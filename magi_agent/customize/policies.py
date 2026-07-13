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
# one first-party policy composed of six member rules (design Section 11).
# The evidence-bound members (claim_citation, evidence_consistency,
# activity_grounding, execution_claims) emit high-confidence findings; the
# heuristic and LLM-skeptic members (sycophancy_heuristics, skeptic_review) emit
# advisory findings.
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
        "ledger contradictions, ungrounded action claims, and fabricated or "
        "failed subagent-execution claims audited against the spawn ledger); "
        "advisory findings flag sycophancy and overconfidence heuristics. All "
        "findings are durable "
        "and observable, with a per-finding resolution status that makes the "
        "ignore-rate measurable -- the core quality signal of this policy."
    ),
    ruleIds=(
        "verify_before_replying.claim_citation",
        "verify_before_replying.evidence_consistency",
        "verify_before_replying.activity_grounding",
        "verify_before_replying.execution_claims",
        "verify_before_replying.sycophancy_heuristics",
        "verify_before_replying.skeptic_review",
    ),
    origin="builtin",
    # Non-blocking nudge: safe to opt out of. Listed in
    # ``builtin_policy_overrides.BUILTIN_POLICY_TOGGLES``.
    userDisableable=True,
)


# The system-safety policy: visibility and audit attribution for the
# hard-safety layer already enforced by tools/safety.py, unconditionally and
# regardless of permission mode. No enforcement change -- the deny layer
# predates this policy; this card names it and makes its denies attributable
# in the audit/evidence trail. Floor: not user-disableable; absent from
# BUILTIN_POLICY_TOGGLES by construction (floors by absence). No PolicyBinding:
# system_safety attribution records are audit records, not unlock evidence.
_SYSTEM_SAFETY_POLICY = Policy(
    id="system_safety",
    displayName="System Safety",
    intent=(
        "Hard runtime denials that protect the machine regardless of "
        "permission mode: destructive shell (recursive rm of /, dd to "
        "devices, mkfs, disk erase, recursive chmod/chown of /), piping "
        "a download into a shell, upload-shaped network commands (scp, "
        "rsync, sftp, piped or upload-argument curl/wget/nc/ssh), "
        "workspace path confinement with secret-path and sealed-file "
        "guards, shell hygiene denials (path expansion into guarded "
        "paths, mutating or unsafe command flags), and unsafe git "
        "operations. Inline interpreter code (python -c and friends) is "
        "denied in the default posture but allowed under an explicit "
        "bypassPermissions scope, which is the operator's own machine "
        "and choice. Detection is command-string analysis: it is a "
        "strong guard against agent mistakes and casual injection, not "
        "a sandbox against a determined obfuscated adversary."
    ),
    ruleIds=(
        "system_safety.destructive_shell",
        "system_safety.curl_pipe_exec",
        "system_safety.network_exfiltration",
        "system_safety.inline_interpreter",
        "system_safety.workspace_confinement",
        "system_safety.secret_paths",
        "system_safety.shell_hygiene",
        "system_safety.unsafe_git",
    ),
    origin="builtin",
    # Floor: enforcement is unconditional in tools/safety.py and fires even
    # under bypassPermissions. Absent from BUILTIN_POLICY_TOGGLES.
    userDisableable=False,
)


# The injection_guard policy: deterministic prompt-injection heuristics over
# EXTERNAL tool-result content (web/browser/KB reads) expressed as one
# first-party policy (design Section 6). The ``scan`` member records findings as
# ``custom:InjectionSuspicion`` evidence; the ``annotate`` member prepends a
# static advisory header on HIGH severity and neutralizes spoofed in-content
# markers; the ``nudge`` member (U7) adds an optional pre-final advisory nudge.
# No PolicyBinding: injection findings are audit records, not unlock evidence,
# and must never satisfy an evidence gate (design Section 11 note 3). Non-blocking
# (never blocks, never rewrites fetched content), so it is user-disableable and
# listed in ``builtin_policy_overrides.BUILTIN_POLICY_TOGGLES``.
_INJECTION_GUARD_POLICY = Policy(
    id="injection_guard",
    displayName="Injection Guard",
    intent=(
        "Scan external tool-result content (web search, web fetch, browser "
        "reads, knowledge-base search) for prompt-injection heuristics: "
        "instruction overrides, role or system spoofing, exfiltration and "
        "command lures, credential harvesting, hidden-text carriers, and Korean "
        "variants. Findings are recorded as audit evidence; on a high-severity "
        "match a static advisory header is prepended to the content so the model "
        "treats it as untrusted data, and any spoofed copy of this runtime's own "
        "banner inside the content is neutralized. Detection is deterministic "
        "pattern matching: a strong guard against casual injection and agent "
        "mistakes, not a sandbox against a determined obfuscated adversary. It "
        "never blocks the turn and never rewrites the fetched content itself."
    ),
    ruleIds=(
        "injection_guard.scan",
        "injection_guard.annotate",
        "injection_guard.nudge",
    ),
    origin="builtin",
    # Non-blocking advisory: safe to opt out of. Listed in
    # ``builtin_policy_overrides.BUILTIN_POLICY_TOGGLES``.
    userDisableable=True,
)

BUILTIN_POLICIES: tuple[Policy, ...] = (
    _INJECTION_GUARD_POLICY,
    _SOURCE_CITATION_POLICY,
    _SYSTEM_SAFETY_POLICY,
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


_FLOOR_IDS: frozenset[str] = frozenset(
    p.policy_id for p in BUILTIN_POLICIES if not p.user_disableable
)


def upsert_policy(policy: Policy, path: Path | None = None) -> None:
    if policy.origin == "builtin":
        raise ValueError("built-in policies are read-only; clone to a new id to customize")
    if policy.policy_id in _FLOOR_IDS:
        raise ValueError(
            f"'{policy.policy_id}' is a builtin floor policy id and cannot be used for "
            "a user policy; choose a different id"
        )
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


# ---------------------------------------------------------------------------
# Auto-promotion: no orphan rules (policies-first surface unification, PR-1)
# ---------------------------------------------------------------------------
#
# The user's unit of intent is a *policy*; a bare rule is an implementation
# detail. Every rule must therefore be reachable through some Policy so the
# management surface never shatters a policy back into loose rules. Two seams
# keep this invariant: promote-on-create (U1, called from the custom-rule save
# path for a genuinely NEW rule) and a read-time backfill (U2, for rules that
# predate this feature). Both derive a 1-rule Policy the same way.


def _referenced_rule_ids(path: Path | None) -> set[str]:
    """Every rule id referenced by any persisted user policy (its ``ruleIds``).

    Builtins are excluded on purpose: their member ids live in the
    ``<policy>.<member>`` namespace and never collide with user custom-rule ids,
    and a stored user policy is what determines whether a rule is already
    surfaced. Reads the raw store so a malformed entry cannot mask a reference.
    """
    referenced: set[str] = set()
    for raw in _policies_raw(path).values():
        if not isinstance(raw, dict):
            continue
        rule_ids = raw.get("ruleIds")
        if isinstance(rule_ids, list):
            for rid in rule_ids:
                if isinstance(rid, str):
                    referenced.add(rid)
    return referenced


def _derive_policy_id_for_rule(rule_id: str, taken: set[str]) -> str:
    """A collision-free bare-slug policy id derived from a rule id.

    Slugifies the rule id (the natural, stable name) and, on collision with an
    already-taken policy id, appends ``-2``, ``-3``, … until free. The suffix is
    bounded so the result always satisfies ``_POLICY_ID_RE``.
    """
    base = _slugify(rule_id)
    if base not in taken:
        return base
    for suffix in range(2, 10_000):
        candidate = f"{base[:56]}-{suffix}"
        if candidate not in taken:
            return candidate
    # Astronomically unlikely; fall back to a hash-ish tail.
    return f"{base[:48]}-{abs(hash(rule_id)) % 1_000_000}"


def promote_rule_to_policy(
    rule: dict,
    *,
    path: Path | None = None,
    display_name: str | None = None,
    intent: str | None = None,
) -> str | None:
    """Persist a 1-rule Policy for a freshly-created custom rule (U1).

    Idempotent guard: returns ``None`` (creating nothing) when the rule has no
    usable id OR the rule id is already referenced by some persisted policy — so
    an UPDATE to an existing rule, or a save that ``persist_policy_plan`` already
    tied into a Policy, never double-creates.

    ``display_name`` defaults to the rule id (custom rules carry no name field;
    matches the read-time ``implicit_policy_for_rule`` view). ``intent`` defaults
    to empty. When the caller has the original natural-language text (the
    conversational compile flow), it should pass it as ``intent`` so the policy
    card shows the user's own sentence.

    Returns the new policy id, or ``None`` when nothing was created.
    """
    rule_id = rule.get("id") if isinstance(rule, dict) else None
    if not isinstance(rule_id, str) or _RULE_ID_RE.fullmatch(rule_id) is None:
        return None
    if rule_id in _referenced_rule_ids(path):
        return None  # already surfaced through a policy; do not double-create
    taken = {p.policy_id for p in list_policies(path)}
    policy_id = _derive_policy_id_for_rule(rule_id, taken)
    label = (display_name or "").strip() or rule_id
    try:
        policy = Policy(
            id=policy_id,
            displayName=label[:120],
            intent=(intent or ""),
            ruleIds=(rule_id,),
            origin="user",
        )
    except ValidationError:
        return None
    upsert_policy(policy, path)
    return policy_id


def _unreferenced_seam_spec_ids(path: Path | None, referenced: set[str]) -> list[str]:
    """Ids of stored seam specs not referenced by any policy."""
    overrides = load_overrides(path)
    specs = overrides.get("verification", {}).get("seam_specs", [])
    out: list[str] = []
    if isinstance(specs, list):
        for spec in specs:
            if not isinstance(spec, dict):
                continue
            sid = spec.get("id")
            if (
                isinstance(sid, str)
                and _RULE_ID_RE.fullmatch(sid) is not None
                and sid not in referenced
            ):
                out.append(sid)
    return out


def ensure_policies_for_unreferenced_rules(path: Path | None = None) -> int:
    """Idempotently synthesize + persist 1-rule policies for every unreferenced
    rule (U2). Returns the number of policies created.

    Order matters: ``migrate_groups_to_policies`` runs FIRST so a set of grouped
    rules lands in its single multi-rule policy (not N singles). Then, for each
    custom rule / seam spec whose id is not referenced by any persisted policy, a
    1-rule Policy is created (same derivation as ``promote_rule_to_policy``). A
    rule already inside a policy (multi-rule or a prior single) is left
    untouched. Persists only when something changed (a fully-migrated store
    re-runs to a no-op with no write).

    Dashboard-check producers are intentionally NOT backfilled here: their
    sidecar lives at a single host-global writable pack root that is not scoped
    to ``path``, and a producer authored through ``persist_policy_plan`` is
    already bound into a Policy (so it is referenced anyway). Covering them would
    require a path-scoped producer store that does not exist today."""
    created = migrate_groups_to_policies(path)

    referenced = _referenced_rule_ids(path)
    taken = {p.policy_id for p in list_policies(path)}

    candidate_ids: list[str] = []
    for rule in _custom_rules_raw(path):
        if not isinstance(rule, dict):
            continue
        rid = rule.get("id")
        if (
            isinstance(rid, str)
            and _RULE_ID_RE.fullmatch(rid) is not None
            and rid not in referenced
        ):
            candidate_ids.append(rid)
    candidate_ids.extend(_unreferenced_seam_spec_ids(path, referenced))

    seen: set[str] = set()
    for rid in candidate_ids:
        if rid in seen or rid in referenced:
            continue
        seen.add(rid)
        policy_id = _derive_policy_id_for_rule(rid, taken)
        try:
            policy = Policy(
                id=policy_id,
                displayName=rid[:120],
                intent="",
                ruleIds=(rid,),
                origin="user",
            )
        except ValidationError:
            continue
        upsert_policy(policy, path)
        taken.add(policy_id)
        referenced.add(rid)
        created += 1
    return created


# ---------------------------------------------------------------------------
# Policy-level enabled cascade (U4)
# ---------------------------------------------------------------------------


def set_policy_enabled(policy_id: str, enabled: bool, path: Path | None = None) -> int:
    """Set ``enabled`` on every member custom rule of a USER policy (cascade).

    Returns the number of member rules whose flag was written. Raises
    :class:`KeyError` for an unknown policy id and :class:`ValueError` for a
    builtin (first-party) policy — those keep their own preset / control-plane /
    builtin-policy PATCH routes and must not be toggled through this seam.

    A member id that is not a stored custom rule (e.g. a dashboard-check
    producer or a builtin member ref) is skipped silently: the cascade only
    owns the ``verification.custom_rules[]`` ``enabled`` axis.
    """
    policy = get_policy(policy_id, path)
    if policy is None:
        raise KeyError(policy_id)
    if policy.origin != "user":
        raise ValueError(
            "built-in policies are toggled via their own routes, not the policy cascade"
        )
    member_ids = set(policy.rule_ids)
    overrides = load_overrides(path)
    rules = overrides["verification"]["custom_rules"]
    changed = 0
    for rule in rules:
        if isinstance(rule, dict) and rule.get("id") in member_ids:
            rule["enabled"] = bool(enabled)
            changed += 1
    save_overrides(overrides, path)
    return changed
