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

from typing import Literal

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
