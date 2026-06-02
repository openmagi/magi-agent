from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable


MATRIX_PATH = (
    Path(__file__).parent / "fixtures" / "recipe_composition" / "matrix.json"
)

REQUIRED_TOP_LEVEL_FIELDS = {
    "schemaVersion",
    "generatedFor",
    "auditDate",
    "activationMode",
    "rows",
}

REQUIRED_ROW_IDS = {
    "approval_union",
    "auto_recipe_compatibility",
    "conflict_projection",
    "context_least_privilege",
    "default_off_flags",
    "deny_beats_grant",
    "effective_digest",
    "evidence_stricter_merge",
    "explicit_recipe_inclusion",
    "global_retry_cap",
    "hard_safety_wins",
    "hook_ordering_dedupe",
    "non_idempotent_hook_conflict",
    "selector_fallback_block",
}

REQUIRED_ROW_FIELDS = {
    "id",
    "semantic",
    "currentSupport",
    "gap",
    "targetSlice",
    "defaultOff",
    "trafficAttached",
    "executionAttached",
    "selectedRefs",
    "autoRefs",
    "hardSafetyRefs",
    "expectedDecision",
    "expectedOutcome",
    "expectedConflicts",
    "projection",
}

EXPECTED_CONFLICTS = {
    "approval_union": [],
    "auto_recipe_compatibility": [],
    "conflict_projection": ["conflict.provider.choice"],
    "context_least_privilege": [],
    "default_off_flags": [],
    "deny_beats_grant": ["conflict.tool.deniedGrant"],
    "effective_digest": [],
    "evidence_stricter_merge": [],
    "explicit_recipe_inclusion": [],
    "global_retry_cap": [],
    "hard_safety_wins": ["conflict.hardSafety.override"],
    "hook_ordering_dedupe": [],
    "non_idempotent_hook_conflict": ["conflict.hook.nonIdempotent"],
    "selector_fallback_block": ["conflict.selector.fallbackBlocked"],
}

EXPECTED_OUTCOMES: dict[str, dict[str, Any]] = {
    "approval_union": {
        "status": "admit",
        "approvalMerge": "union",
        "includedRefs": ["recipe.alpha", "recipe.beta"],
    },
    "auto_recipe_compatibility": {
        "status": "admit",
        "includedRefs": ["recipe.auto.compatible"],
        "omittedRefs": ["recipe.auto.incompatible"],
        "omissionReasons": {
            "recipe.auto.incompatible": "runtime_incompatible",
        },
    },
    "conflict_projection": {
        "status": "block",
        "includedRefs": [],
        "omittedRefs": ["recipe.beta"],
    },
    "context_least_privilege": {
        "status": "admit",
        "contextExposure": "refs_only",
        "includedRefs": ["recipe.context.broad", "recipe.context.narrow"],
    },
    "default_off_flags": {
        "status": "metadata_only",
        "includedRefs": ["recipe.metadata.only"],
    },
    "deny_beats_grant": {
        "status": "block",
        "toolDecision": "deny",
        "includedRefs": [],
        "omittedRefs": ["recipe.granting"],
    },
    "effective_digest": {
        "status": "admit",
        "digestRequired": True,
        "includedRefs": ["recipe.alpha", "recipe.beta"],
    },
    "evidence_stricter_merge": {
        "status": "admit",
        "evidenceMerge": "union_strictest_missing_blocks",
        "includedRefs": ["recipe.evidence.base", "recipe.evidence.strict"],
    },
    "explicit_recipe_inclusion": {
        "status": "partial_admit_with_fail_closed_omission",
        "requestedRefs": [
            "recipe.explicit.included",
            "recipe.explicit.omitted",
        ],
        "includedRefs": ["recipe.explicit.included"],
        "omittedRefs": ["recipe.explicit.omitted"],
        "omissionReasons": {
            "recipe.explicit.omitted": "required_dependency_disabled",
        },
    },
    "global_retry_cap": {
        "status": "admit",
        "retryCap": 1,
        "includedRefs": ["recipe.retry.wide", "recipe.retry.tight"],
    },
    "hard_safety_wins": {
        "status": "admit",
        "hardSafetyDecision": "enforce",
        "overrideAction": "hard_safety_overrides_weaker_request",
        "effectiveHardSafetyRefs": ["recipe.safety.hard"],
        "overriddenRefs": ["recipe.regular"],
        "downgradeAllowed": False,
        "includedRefs": ["recipe.safety.hard", "recipe.regular"],
    },
    "hook_ordering_dedupe": {
        "status": "admit",
        "includedRefs": ["recipe.hook.alpha", "recipe.hook.beta"],
    },
    "non_idempotent_hook_conflict": {
        "status": "block",
        "includedRefs": [],
        "omittedRefs": ["recipe.hook.second"],
    },
    "selector_fallback_block": {
        "status": "block",
        "governedSelector": {
            "required": True,
            "prohibitedFallbackTarget": "general_chat",
        },
        "fallbackAllowed": False,
        "blockingReason": "governed_selector_required",
        "includedRefs": [],
        "omittedRefs": ["recipe.governed.required", "general_chat"],
        "omissionReasons": {
            "recipe.governed.required": "governed_selector_required",
            "general_chat": "governed_selector_fallback_prohibited",
        },
    },
}

EXPECTED_PROJECTIONS = {
    "approval_union": {
        "publicSafe": True,
        "decisionRef": "decision.approval.union",
        "visibleRefs": ["approval.operator", "approval.owner"],
    },
    "auto_recipe_compatibility": {
        "publicSafe": True,
        "decisionRef": "decision.auto.compatible",
        "visibleRefs": ["recipe.auto.compatible"],
    },
    "conflict_projection": {
        "publicSafe": True,
        "decisionRef": "decision.conflict.projected",
        "visibleRefs": ["conflict.provider.choice"],
    },
    "context_least_privilege": {
        "publicSafe": True,
        "decisionRef": "decision.context.leastPrivilege",
        "visibleRefs": ["context.refsOnly", "context.cap.min"],
    },
    "default_off_flags": {
        "publicSafe": True,
        "decisionRef": "decision.defaultOff.detached",
        "visibleRefs": ["flag.defaultOff", "flag.detached"],
    },
    "deny_beats_grant": {
        "publicSafe": True,
        "decisionRef": "decision.tool.denied",
        "visibleRefs": ["tool.ref.sample", "conflict.tool.deniedGrant"],
    },
    "effective_digest": {
        "publicSafe": True,
        "decisionRef": "decision.digest.effective",
        "visibleRefs": ["digest.effectivePolicy"],
    },
    "evidence_stricter_merge": {
        "publicSafe": True,
        "decisionRef": "decision.evidence.strict",
        "visibleRefs": ["evidence.union", "evidence.missing.blocks"],
    },
    "explicit_recipe_inclusion": {
        "publicSafe": True,
        "decisionRef": "decision.explicit.included",
        "visibleRefs": ["recipe.explicit.included", "recipe.explicit.omitted"],
    },
    "global_retry_cap": {
        "publicSafe": True,
        "decisionRef": "decision.retry.strictestCap",
        "visibleRefs": ["retry.cap.min"],
    },
    "hard_safety_wins": {
        "publicSafe": True,
        "decisionRef": "decision.safety.override",
        "visibleRefs": ["recipe.safety.hard", "conflict.hardSafety.override"],
    },
    "hook_ordering_dedupe": {
        "publicSafe": True,
        "decisionRef": "decision.hook.ordered",
        "visibleRefs": ["hook.beforeModel.alpha", "hook.afterTool.beta"],
    },
    "non_idempotent_hook_conflict": {
        "publicSafe": True,
        "decisionRef": "decision.hook.conflict",
        "visibleRefs": ["conflict.hook.nonIdempotent"],
    },
    "selector_fallback_block": {
        "publicSafe": True,
        "decisionRef": "decision.selector.fallbackBlocked",
        "visibleRefs": ["conflict.selector.fallbackBlocked"],
    },
}

FORBIDDEN_FIELD_NAMES = {
    "hiddenConfig",
    "private",
    "raw",
    "runtimeConfig",
    "secret",
    "toolArguments",
    "toolArgs",
    "toolResult",
    "toolResults",
    "transcript",
}
FORBIDDEN_FIELD_NAME_NORMALIZED = {
    re.sub(r"[^a-zA-Z0-9]", "", field).lower() for field in FORBIDDEN_FIELD_NAMES
}
FORBIDDEN_STRING_CONCEPTS = (
    "hidden config",
    "private",
    "raw prompt",
    "raw tool",
    "runtime config",
    "secret",
    "secret material",
    "tool arguments",
    "tool results",
    "transcript",
)
PRIVATE_PATH_RE = re.compile(r"(^|[\s:=])(/Users/|/home/|/private/|/var/folders/)")
SECRET_SHAPED_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,}|"
    r"xox[baprs]-[A-Za-z0-9-]{16,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)


def _load_matrix() -> dict[str, Any]:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, nested in value.items():
            yield str(key)
            yield from _walk_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_keys(nested)


def test_matrix_has_required_top_level_fields_and_locked_row_ids() -> None:
    matrix = _load_matrix()

    assert set(matrix) == REQUIRED_TOP_LEVEL_FIELDS
    assert matrix["schemaVersion"] == "recipeCompositionMatrix.v1"
    assert matrix["generatedFor"] == "multi-recipe-composition-pr0"
    assert matrix["auditDate"] == "2026-05-26"
    assert matrix["activationMode"] == {
        "defaultOff": True,
        "trafficAttached": False,
        "executionAttached": False,
        "liveActivation": False,
    }
    assert isinstance(matrix["rows"], list)

    row_ids = [row["id"] for row in matrix["rows"]]
    assert row_ids == sorted(row_ids)
    assert len(row_ids) == len(set(row_ids))
    assert set(row_ids) == REQUIRED_ROW_IDS


def test_all_rows_are_default_off_and_detached_from_live_execution() -> None:
    for row in _load_matrix()["rows"]:
        assert set(row) == REQUIRED_ROW_FIELDS, row["id"]
        assert row["defaultOff"] is True, row["id"]
        assert row["trafficAttached"] is False, row["id"]
        assert row["executionAttached"] is False, row["id"]


def test_conflict_rows_lock_exact_expected_refs_and_denial_behavior() -> None:
    rows = {row["id"]: row for row in _load_matrix()["rows"]}

    assert set(EXPECTED_CONFLICTS) == REQUIRED_ROW_IDS
    for row_id, expected_conflicts in EXPECTED_CONFLICTS.items():
        assert rows[row_id]["expectedConflicts"] == expected_conflicts

    deny_decision = rows["deny_beats_grant"]["expectedDecision"].lower()
    assert "deny" in deny_decision
    assert "tool" in deny_decision
    assert rows["deny_beats_grant"]["expectedOutcome"]["toolDecision"] == "deny"


def test_structured_expected_outcomes_are_locked() -> None:
    rows = {row["id"]: row for row in _load_matrix()["rows"]}

    for row_id, expected in EXPECTED_OUTCOMES.items():
        outcome = rows[row_id]["expectedOutcome"]
        assert outcome == expected, row_id
        row_input_refs = set(rows[row_id]["selectedRefs"]) | set(rows[row_id]["autoRefs"])
        assert set(outcome.get("includedRefs", ())) <= row_input_refs, row_id
        assert set(outcome.get("omittedRefs", ())) <= row_input_refs, row_id
        assert set(outcome.get("requestedRefs", ())) <= row_input_refs, row_id


def test_projection_is_public_safe() -> None:
    for row in _load_matrix()["rows"]:
        assert set(row["projection"]) == {"publicSafe", "decisionRef", "visibleRefs"}
        assert row["projection"] == EXPECTED_PROJECTIONS[row["id"]], row["id"]


def test_fixture_recursively_rejects_forbidden_fields_paths_and_secrets() -> None:
    matrix = _load_matrix()

    for key in _walk_keys(matrix):
        normalized = re.sub(r"[^a-zA-Z0-9]", "", key).lower()
        for forbidden in FORBIDDEN_FIELD_NAME_NORMALIZED:
            assert forbidden not in normalized, key

    for value in _walk(matrix):
        if isinstance(value, str):
            lowered = value.lower()
            for concept in FORBIDDEN_STRING_CONCEPTS:
                assert concept not in lowered, value
            assert PRIVATE_PATH_RE.search(value) is None, value
            assert SECRET_SHAPED_RE.search(value) is None, value
