from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


SelectorVerdictStatus = Literal["pass", "fail"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_SAFE_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:@-]{0,180}$")
_PROTECTED_REF_FRAGMENTS = (
    "author" + "ization",
    "coo" + "kie",
    "to" + "ken",
    "se" + "cret",
    "api_" + "key",
    "pass" + "word",
    "pro" + "mpt",
    "sess" + "ion",
    "priv" + "ate",
    "bearer",
    "credential",
    "oauth",
)
_RAW_REF_MARKERS = (
    "raw:",
    "rawref",
    "rawtoollog",
    "rawchildtranscript",
    "rawoutput",
    "hiddenreasoning",
)


class RecipeSelectorFixture(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    input_text: str = Field(alias="inputText")
    expected_governed: StrictBool = Field(default=False, alias="expectedGoverned")
    actual_governed: StrictBool = Field(default=False, alias="actualGoverned")
    expected_route_ref: str | None = Field(default=None, alias="expectedRouteRef")
    actual_route_ref: str | None = Field(default=None, alias="actualRouteRef")
    expected_workflow_ref: str | None = Field(default=None, alias="expectedWorkflowRef")
    actual_workflow_ref: str | None = Field(default=None, alias="actualWorkflowRef")
    selected_recipe_ids: tuple[str, ...] = Field(default=(), alias="selectedRecipeIds")
    expected_recipe_ids: tuple[str, ...] = Field(default=(), alias="expectedRecipeIds")
    must_enable: tuple[str, ...] = Field(default=(), alias="mustEnable")
    enabled_refs: tuple[str, ...] = Field(default=(), alias="enabledRefs")
    must_deny: tuple[str, ...] = Field(default=(), alias="mustDeny")
    denied_refs: tuple[str, ...] = Field(default=(), alias="deniedRefs")
    forbidden_recipe_ids: tuple[str, ...] = Field(default=(), alias="forbiddenRecipeIds")

    @field_validator(
        "expected_route_ref",
        "actual_route_ref",
        "expected_workflow_ref",
        "actual_workflow_ref",
    )
    @classmethod
    def _validate_public_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if not clean or not _SAFE_PUBLIC_REF_RE.fullmatch(clean):
            raise ValueError("selector route/workflow refs must be safe public metadata refs")
        lowered = clean.lower()
        if (
            any(fragment in lowered for fragment in _PROTECTED_REF_FRAGMENTS)
            or any(marker in lowered for marker in _RAW_REF_MARKERS)
            or "/" in clean
            or "\\" in clean
            or clean.startswith(("~", "."))
        ):
            raise ValueError("selector route/workflow refs contain protected runtime data markers")
        return clean


class RecipeSelectorVerdict(BaseModel):
    model_config = _MODEL_CONFIG

    status: SelectorVerdictStatus
    fixture_id: str = Field(alias="fixtureId")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    missing_recipe_ids: tuple[str, ...] = Field(default=(), alias="missingRecipeIds")
    missing_enable_refs: tuple[str, ...] = Field(default=(), alias="missingEnableRefs")
    missing_deny_refs: tuple[str, ...] = Field(default=(), alias="missingDenyRefs")
    forbidden_selected_recipe_ids: tuple[str, ...] = Field(
        default=(),
        alias="forbiddenSelectedRecipeIds",
    )
    expected_governed: StrictBool = Field(default=False, alias="expectedGoverned")
    actual_governed: StrictBool = Field(default=False, alias="actualGoverned")
    governance_mismatch: StrictBool = Field(default=False, alias="governanceMismatch")


def evaluate_recipe_selector_fixture(fixture: RecipeSelectorFixture) -> RecipeSelectorVerdict:
    selected = set(fixture.selected_recipe_ids)
    enabled = set(fixture.enabled_refs)
    denied = set(fixture.denied_refs)
    missing_recipe_ids = tuple(
        recipe_id for recipe_id in fixture.expected_recipe_ids if recipe_id not in selected
    )
    missing_enable_refs = tuple(ref for ref in fixture.must_enable if ref not in enabled)
    missing_deny_refs = tuple(ref for ref in fixture.must_deny if ref not in denied)
    forbidden_recipe_ids = tuple(
        recipe_id for recipe_id in fixture.forbidden_recipe_ids if recipe_id in selected
    )
    forbidden_selected_recipe_ids = forbidden_recipe_ids
    governance_mismatch = fixture.expected_governed and not fixture.actual_governed
    reason_codes: list[str] = []
    if missing_recipe_ids:
        reason_codes.append("expected_recipe_missing")
    if missing_enable_refs:
        reason_codes.append("required_enable_ref_missing")
    if missing_deny_refs:
        reason_codes.append("required_deny_ref_missing")
    if forbidden_selected_recipe_ids:
        reason_codes.append("forbidden_recipe_selected")
    if governance_mismatch:
        reason_codes.append("governed_selector_resolved_non_governed")
    return RecipeSelectorVerdict(
        status="fail" if reason_codes else "pass",
        fixtureId=fixture.fixture_id,
        reasonCodes=tuple(reason_codes) or ("selector_fixture_passed",),
        missingRecipeIds=missing_recipe_ids,
        missingEnableRefs=missing_enable_refs,
        missingDenyRefs=missing_deny_refs,
        forbiddenSelectedRecipeIds=forbidden_selected_recipe_ids,
        expectedGoverned=fixture.expected_governed,
        actualGoverned=fixture.actual_governed,
        governanceMismatch=governance_mismatch,
    )


__all__ = [
    "RecipeSelectorFixture",
    "RecipeSelectorVerdict",
    "SelectorVerdictStatus",
    "evaluate_recipe_selector_fixture",
]
