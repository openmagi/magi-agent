"""Compile dashboard-authored custom checks into a single user pack.

A "check" is a UI-friendly after-tool match condition. Enforcement is
**deny-on-present** (mirrors the SHACL constraint verifier):

- ``DashboardProducerControl`` (after-tool) reads the sidecar
  ``dashboard-checks.json`` and, on a match, emits an ``EvidenceRecord`` with
  top-level ``status="failed"`` for ``block`` checks (a violation) or
  ``status="ok"`` for ``audit`` checks (observability).
- The pre-final verifier-bus dashboard gate blocks the final answer when a
  failed dashboard record is present; no match / tool-not-run → no record → no
  block.

The recipe pack itself is declarative-only — a discoverable namespace artifact
with EMPTY ``evidenceRefs``. The ``RecipePackManifest.evidence_refs``
required-evidence path is inert here (a ``defaultEnabled=false`` pack is never
auto-selected) AND would invert polarity if selected, so it is NOT used for
enforcement. The pack `evidence_producer` provides type is impl-only
(``packs/manifest.py``), so the declarative producer lives in the sidecar.
R1/R4/R6/R7 still apply to the recipe pack.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from magi_agent.recipes.compiler import RecipePackManifest

DASHBOARD_PACK_DIR_NAME = "dashboard-authored"
DASHBOARD_PACK_ID = "ext.dashboard.checks"
DASHBOARD_EVIDENCE_REF_PREFIX = "evidence:dashboard:"

DashboardScope = Literal["always", "coding", "research", "delivery"]
DashboardAction = Literal["block", "audit"]

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class DashboardTriggerMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    pattern: str
    is_regex: bool = Field(default=False, alias="isRegex")


class DashboardTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    tool: str
    # A result-text match (the historic trigger). Optional now that an
    # arguments-based ``domain_allowlist`` trigger exists; at least one must be
    # present (enforced by validate_dashboard_check).
    match: DashboardTriggerMatch | None = None
    # An ARGUMENTS-based domain allowlist. When set, the producer fires on the
    # tool's URL-argument host (NOT the attacker-controlled result text): a
    # deterministic, unlock-eligible credibility signal. See the policy-
    # abstraction security model (arguments, not returned content).
    domain_allowlist: tuple[str, ...] = Field(default=(), alias="domainAllowlist")


class DashboardCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    label: str
    scope: DashboardScope
    enabled: bool
    trigger: DashboardTrigger
    action: DashboardAction
    # Optional operator-named evidence type this check emits (``custom:PascalCase``).
    # Absent → the historic hardcoded ``custom:DashboardCheck``.
    emits_evidence_type: str | None = Field(default=None, alias="emitsEvidenceType")

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            raise ValueError(
                "id must be lowercase alphanumeric+hyphen+underscore, "
                "1-63 chars, first char alphanumeric"
            )
        return value

    @field_validator("emits_evidence_type")
    @classmethod
    def _validate_emits_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from magi_agent.evidence.types import validate_evidence_type_name  # noqa: PLC0415

        # A dashboard-authored producer may only emit an operator-named
        # ``custom:`` type. Reusing ``validate_evidence_type_name`` alone would
        # accept trusted builtin names (``TestRun``/``WebSearch``/…), letting a
        # domain-allowlist producer mint a record typed as a trusted evidence
        # family (type-confusion into the exact source-credibility signals the
        # policy stack governs). Restrict to the ``custom:`` namespace here.
        if not value.startswith("custom:"):
            raise ValueError(
                "emitsEvidenceType must be an operator-named custom: type "
                "(built-in evidence types are runtime-reserved)"
            )
        return validate_evidence_type_name(value)


_LABEL_MAX = 200
_PATTERN_MAX = 500
_SCOPES: frozenset[str] = frozenset({"always", "coding", "research", "delivery"})
_ACTIONS: frozenset[str] = frozenset({"block", "audit"})

# Catastrophic-backtracking heuristic patterns. Not exhaustive; v1 cap only.
_CATASTROPHIC_REGEX = re.compile(r"\([^)]*[+*]\)[+*]|\([^)]*\|[^)]*\)[+*]")


_ALLOWED_TOP_KEYS: frozenset[str] = frozenset(
    {"id", "label", "scope", "enabled", "trigger", "action", "emitsEvidenceType"}
)
_ALLOWED_TRIGGER_KEYS: frozenset[str] = frozenset({"tool", "match", "domainAllowlist"})
_ALLOWED_MATCH_KEYS: frozenset[str] = frozenset({"pattern", "isRegex", "is_regex"})
_DOMAIN_ALLOWLIST_MAX = 64


def validate_dashboard_check(rule: Any) -> list[str]:
    """Return a list of validation errors (empty = valid)."""
    errors: list[str] = []
    if not isinstance(rule, dict):
        return ["rule must be an object"]

    for key in rule:
        if key not in _ALLOWED_TOP_KEYS:
            errors.append(f"unknown key: {key!r}")

    rid = rule.get("id")
    if not isinstance(rid, str) or not _ID_RE.fullmatch(rid):
        errors.append(
            "id must be lowercase alphanumeric+hyphen+underscore, 1-63 chars, first char alphanumeric"
        )

    label = rule.get("label")
    if not isinstance(label, str) or not label.strip():
        errors.append("label is required")
    elif len(label) > _LABEL_MAX:
        errors.append(f"label exceeds the {_LABEL_MAX}-char cap")
    elif "\n" in label or "\r" in label:
        errors.append("label cannot contain newline characters")

    if rule.get("scope") not in _SCOPES:
        errors.append(f"scope must be one of {sorted(_SCOPES)}")

    if not isinstance(rule.get("enabled"), bool):
        errors.append("enabled must be a boolean")

    if rule.get("action") not in _ACTIONS:
        errors.append(f"action must be one of {sorted(_ACTIONS)}")

    emits_type = rule.get("emitsEvidenceType")
    if emits_type is not None:
        from magi_agent.evidence.types import (  # noqa: PLC0415
            validate_evidence_type_name,
        )

        if not isinstance(emits_type, str):
            errors.append("emitsEvidenceType must be a string")
        elif not emits_type.startswith("custom:"):
            # Only operator-named custom: types (never runtime-reserved builtins);
            # mirror the DashboardCheck._validate_emits_type restriction so a
            # domain-allowlist producer cannot mint a trusted-typed record.
            errors.append(
                "emitsEvidenceType must be an operator-named custom: type "
                "(built-in evidence types are runtime-reserved)"
            )
        else:
            try:
                validate_evidence_type_name(emits_type)
            except ValueError as exc:
                errors.append(f"emitsEvidenceType invalid: {exc}")

    trigger = rule.get("trigger")
    if not isinstance(trigger, dict):
        return [*errors, "trigger must be an object"]
    for key in trigger:
        if key not in _ALLOWED_TRIGGER_KEYS:
            errors.append(f"unknown key under trigger: {key!r}")
    tool = trigger.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        errors.append("trigger.tool is required")

    match = trigger.get("match")
    domain_allowlist = trigger.get("domainAllowlist")
    has_match = match is not None
    has_domain = domain_allowlist is not None
    if not has_match and not has_domain:
        errors.append("trigger requires a match or a domainAllowlist")

    # Arguments-based domain-allowlist trigger (deterministic, unlock-eligible).
    if has_domain:
        if (
            not isinstance(domain_allowlist, list)
            or not domain_allowlist
            or not all(isinstance(d, str) and d.strip() for d in domain_allowlist)
        ):
            errors.append("trigger.domainAllowlist must be a non-empty list of non-empty strings")
        elif len(domain_allowlist) > _DOMAIN_ALLOWLIST_MAX:
            errors.append(
                f"trigger.domainAllowlist exceeds the {_DOMAIN_ALLOWLIST_MAX}-entry cap"
            )

    # Result-text match trigger (historic; now optional).
    if has_match:
        if not isinstance(match, dict):
            return [*errors, "trigger.match must be an object"]
        for key in match:
            if key not in _ALLOWED_MATCH_KEYS:
                errors.append(f"unknown key under trigger.match: {key!r}")
        pattern = match.get("pattern")
        is_regex = match.get("isRegex", False) or match.get("is_regex", False)
        if not isinstance(pattern, str) or not pattern.strip():
            errors.append("trigger.match.pattern is required")
        elif len(pattern) > _PATTERN_MAX:
            errors.append(f"trigger.match.pattern exceeds the {_PATTERN_MAX}-char cap")
        elif is_regex:
            try:
                re.compile(pattern)
            except re.error:
                errors.append("trigger.match.pattern is not a valid regex")
            else:
                if _CATASTROPHIC_REGEX.search(pattern):
                    errors.append("trigger.match.pattern is a potentially catastrophic regex (nested quantifier)")
        if not isinstance(is_regex, bool):
            errors.append("trigger.match.isRegex must be a boolean")

    return errors


_SLUG_NORMALIZE = re.compile(r"[^a-z0-9]+")


def slug_of(label: str, *, taken: set[str] | None = None) -> str:
    """Convert a label to a safe slug; append ``-N`` on collision with ``taken``."""
    base = _SLUG_NORMALIZE.sub("-", (label or "").lower()).strip("-")
    if not base:
        base = "check"
    if taken is None or base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


DASHBOARD_PACK_VERSION = "1"


def _evidence_ref(check_id: str) -> str:
    # The evidence ref keys off ``check.id`` (NOT ``slug_of(label)``), so PR3's
    # producer MUST emit ``evidence:dashboard:<check.id>`` for the validator ref
    # to line up. ``slug_of`` stays for later PRs / the frontend.
    return f"{DASHBOARD_EVIDENCE_REF_PREFIX}{check_id}"


def compile_recipe(checks: list[DashboardCheck]) -> RecipePackManifest:
    """Return the recipe pack manifest — a declarative/discoverable namespace artifact.

    The recipe pack carries NO required evidence refs. Enforcement is
    **deny-on-present**: ``DashboardProducerControl`` (after-tool) emits an
    ``EvidenceRecord`` with top-level ``status="failed"`` when a ``block`` check
    matches, and the pre-final verifier-bus dashboard gate blocks the final
    answer when such a record is present. ``audit`` checks emit ``status="ok"``
    (observability, never blocks).

    A required-evidence ref would be inert (a ``defaultEnabled=false`` pack is
    never auto-selected) AND would invert polarity if ever selected, so
    ``evidenceRefs`` is ALWAYS empty here. ``_evidence_ref`` /
    ``DASHBOARD_EVIDENCE_REF_PREFIX`` survive — the producer stamps that ref into
    each record's ``fields`` so the gate can surface ruleIds.
    """
    return RecipePackManifest(
        packId=DASHBOARD_PACK_ID,
        version=DASHBOARD_PACK_VERSION,
        displayName="Dashboard custom checks",
        description="User-authored custom evidence checks composed via the dashboard.",
        defaultEnabled=False,
        evidenceRefs=(),
    )


def _atomic_write_text(target: Path, text: str) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _atomic_write_json(target: Path, payload: object) -> None:
    _atomic_write_text(target, json.dumps(payload, indent=2, default=str) + "\n")


def _toml_basic_string(value: str) -> str:
    """Serialize a string as a TOML basic string (defensive escaping)."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _serialize_recipe_toml(manifest: RecipePackManifest) -> str:
    """Hand-serialize the recipe manifest to TOML (dependency-free).

    ``parse_recipe_manifest`` (kernel_recipe_packs.py) loads recipe specs with
    ``tomllib.load`` (TOML-only) and is fail-closed — a JSON spec or any parse
    error silently drops the pack. We emit camelCase fields matching the
    hand-authored first-party style (recipe_authoring_static/...). All values are
    derived from ``manifest`` so ``compile_recipe`` stays the single source.
    """
    refs = ", ".join(_toml_basic_string(ref) for ref in manifest.evidence_refs)
    return (
        f"packId = {_toml_basic_string(manifest.pack_id)}\n"
        f"version = {_toml_basic_string(manifest.version)}\n"
        f"displayName = {_toml_basic_string(manifest.display_name)}\n"
        f"description = {_toml_basic_string(manifest.description)}\n"
        f"defaultEnabled = {'true' if manifest.default_enabled else 'false'}\n"
        f"evidenceRefs = [ {refs} ]\n"
    )


_PACK_TOML = (
    'packId = "ext.dashboard.checks"\n'
    'displayName = "Dashboard custom checks"\n'
    'version = "1"\n'
    'description = "User-authored custom evidence checks composed via the dashboard."\n'
    '\n'
    '[[provides]]\n'
    'type = "recipe"\n'
    'ref = "recipe:ext.dashboard.checks@1"\n'
    'spec = "checks.recipe.toml"\n'
)


def write_pack(packs_root: Path, checks: list[DashboardCheck]) -> None:
    """Write the dashboard pack to ``packs_root``.

    Each file is written with an atomic replace; ``pack.toml`` is written LAST so
    discovery never observes a manifest pointing at a missing or half-written
    spec. (This is not whole-pack atomicity — individual files land atomically.)
    Empty ``checks`` removes the directory entirely so FS discovery sees nothing
    (byte-identical baseline).
    """
    if not checks:
        if packs_root.exists():
            shutil.rmtree(packs_root)
        return
    packs_root.mkdir(parents=True, exist_ok=True)
    # 1. recipe spec (validator side) — TOML so parse_recipe_manifest can load it.
    manifest = compile_recipe(checks)
    _atomic_write_text(
        packs_root / "checks.recipe.toml", _serialize_recipe_toml(manifest)
    )
    # 2. sidecar (producer side) — JSON, read by our own read_sidecar.
    _atomic_write_json(
        packs_root / "dashboard-checks.json",
        [c.model_dump(by_alias=True) for c in checks],
    )
    # 3. pack.toml LAST so discovery never sees a manifest pointing at a missing spec
    _atomic_write_text(packs_root / "pack.toml", _PACK_TOML)


def read_sidecar(packs_root: Path) -> list[DashboardCheck]:
    """Load the sidecar check list. Missing file → []."""
    sidecar = packs_root / "dashboard-checks.json"
    if not sidecar.exists():
        return []
    try:
        raw = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    out: list[DashboardCheck] = []
    for item in raw:
        try:
            out.append(DashboardCheck.model_validate(item))
        except Exception:  # noqa: BLE001 — skip malformed entries
            continue
    return out
