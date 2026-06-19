"""Compile dashboard-authored custom checks into a single user pack.

A "check" is a UI-friendly pair: an after-tool match condition (the *producer*
half — what makes evidence appear) and the validator that requires that evidence
to be absent (block) or merely audits it. The producer side lives in a sidecar
``dashboard-checks.json`` read by ``DashboardProducerControl`` at runtime; the
validator side rides through the standard pack path (a ``recipe`` provides entry
whose ``RecipePackManifest.evidence_refs`` requires ``evidence:dashboard:<slug>``).

The pack `evidence_producer` provides type is impl-only (``packs/manifest.py``),
so a declarative producer cannot be expressed as a pack provides entry — hence
the sidecar split. R1/R4/R6/R7 still apply to the recipe pack.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DASHBOARD_PACK_DIR_NAME = "dashboard-authored"
DASHBOARD_PACK_ID = "ext.dashboard.checks"
DASHBOARD_EVIDENCE_REF_PREFIX = "evidence:dashboard:"

DashboardScope = Literal["always", "coding", "research", "delivery"]
DashboardAction = Literal["block", "audit"]


class DashboardTriggerMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    pattern: str
    is_regex: bool = Field(default=False, alias="isRegex")


class DashboardTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    tool: str
    match: DashboardTriggerMatch


class DashboardCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    label: str
    scope: DashboardScope
    enabled: bool
    trigger: DashboardTrigger
    action: DashboardAction


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_LABEL_MAX = 200
_PATTERN_MAX = 500
_SCOPES: frozenset[str] = frozenset({"always", "coding", "research", "delivery"})
_ACTIONS: frozenset[str] = frozenset({"block", "audit"})

# Catastrophic-backtracking heuristic patterns. Not exhaustive; v1 cap only.
_CATASTROPHIC_REGEX = re.compile(r"\([^)]*[+*]\)[+*]|\([^)]*\|[^)]*\)[+*]")


def validate_dashboard_check(rule: Any) -> list[str]:
    """Return a list of validation errors (empty = valid)."""
    errors: list[str] = []
    if not isinstance(rule, dict):
        return ["rule must be an object"]

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

    trigger = rule.get("trigger")
    if not isinstance(trigger, dict):
        return [*errors, "trigger must be an object"]
    tool = trigger.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        errors.append("trigger.tool is required")

    match = trigger.get("match")
    if not isinstance(match, dict):
        return [*errors, "trigger.match must be an object"]
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
