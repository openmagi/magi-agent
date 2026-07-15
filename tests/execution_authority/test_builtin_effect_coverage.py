from __future__ import annotations

import json
from pathlib import Path

import pytest

from magi_agent.execution_authority.adapters.tool_manifest import (
    EffectInventoryError,
    audit_effect_coverage,
    discover_effect_surfaces,
    load_expected_effect_inventory,
)


_REQUIRED_CATEGORIES = {
    "adapter",
    "artifact",
    "browser",
    "child",
    "database",
    "filesystem",
    "git",
    "hook",
    "http_provider",
    "infra",
    "knowledge",
    "mcp_custom",
    "memory",
    "message",
    "mission",
    "scheduler",
    "shell_python",
}

_REQUIRED_BYPASS_CASES = {
    "artifact_delivery",
    "browser_action",
    "child_execution",
    "hook_execution",
    "http_provider_call",
    "infra_action",
    "inline_python",
    "kb_write",
    "mcp_custom_dispatch",
    "memory_write",
    "message_delivery",
    "patch_apply",
    "scheduler_write",
    "shell_cp",
    "shell_redirection",
    "shell_touch",
}


def test_every_effect_capable_surface_has_one_reviewed_resolution() -> None:
    expected = load_expected_effect_inventory()

    assert expected.surfaces
    assert expected.registrations
    report = audit_effect_coverage(expected=expected)

    assert report.discovered == expected.surfaces
    assert report.missing == ()
    assert report.stale == ()
    assert report.duplicates == ()
    assert report.handler_digest_drift == ()
    assert report.invalid_resolutions == ()


def test_inventory_covers_all_effect_classes_and_bypass_sentinels() -> None:
    expected = load_expected_effect_inventory()

    covered_categories = {
        *(surface.category for surface in expected.surfaces),
        *(case.category for case in expected.bypass_cases if case.disposition == "hard_reject"),
    }
    assert _REQUIRED_CATEGORIES <= covered_categories
    assert _REQUIRED_BYPASS_CASES <= {case.case_id for case in expected.bypass_cases}
    assert {case.disposition for case in expected.bypass_cases} <= {
        "broker_registration",
        "hard_reject",
    }


def test_discovery_is_independent_and_detects_a_new_direct_mutator(tmp_path: Path) -> None:
    package = tmp_path / "magi_agent"
    package.mkdir()
    (package / "new_escape.py").write_text(
        "from pathlib import Path\n\n"
        "def unreviewed_effect(target: Path) -> None:\n"
        "    target.write_text('escaped', encoding='utf-8')\n",
        encoding="utf-8",
    )

    discovered = discover_effect_surfaces(source_root=tmp_path)

    assert len(discovered) == 1
    assert discovered[0].source_path == "magi_agent/new_escape.py"
    assert discovered[0].symbol == "unreviewed_effect"
    assert discovered[0].primitive == "path.write_text"


@pytest.mark.parametrize("missing_field", ["owner", "reason", "enforcement"])
def test_exemptions_without_review_metadata_are_mechanically_rejected(
    tmp_path: Path,
    missing_field: str,
) -> None:
    inventory = {
        "schemaVersion": 1,
        "sourceRoot": "magi_agent",
        "registrations": [],
        "exemptions": [
            {
                "id": "exemption:test",
                "owner": "execution-authority",
                "reason": "bounded read-only fixture inspection",
                "enforcement": "read_only_source_audit",
                "scope": ["magi_agent/example.py"],
            }
        ],
        "surfaces": [],
        "bypassCases": [],
    }
    del inventory["exemptions"][0][missing_field]
    path = tmp_path / "invalid-inventory.json"
    path.write_text(json.dumps(inventory), encoding="utf-8")

    with pytest.raises(EffectInventoryError, match=missing_field):
        load_expected_effect_inventory(path=path)


def test_mandatory_live_admission_topology_is_pinned() -> None:
    expected = load_expected_effect_inventory()
    boundary_symbols = {
        (surface.source_path, surface.symbol)
        for surface in expected.surfaces
        if "mandatory_boundary" in surface.detector.split("+")
    }

    assert {
        ("magi_agent/tools/dispatcher.py", "ToolDispatcher._dispatch_inner"),
        ("magi_agent/tools/permission.py", "ToolPermissionPolicy._decide"),
        ("magi_agent/tools/safety.py", "RuntimePermissionArbiter.decide"),
        ("magi_agent/tools/safety.py", "_preflight"),
        ("magi_agent/tools/safety.py", "_read_ledger_preflight"),
        ("magi_agent/gates/gate5b_full_toolhost.py", "Gate5BFullToolHost.dispatch"),
        (
            "magi_agent/gates/gate5b_full_toolhost.py",
            "Gate5BFullToolHost._preflight_legacy_tool",
        ),
        (
            "magi_agent/firstparty/packs/gates_policy_default/impl.py",
            "permission_preflight_policy",
        ),
    } <= boundary_symbols
