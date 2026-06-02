from __future__ import annotations

from pathlib import Path

from magi_agent.recipes.first_party.general_automation.preset_projection import (
    MUTATION_TOOL_CATEGORIES,
    compile_general_automation_presets,
    project_general_automation_preset,
)
from magi_agent.recipes.first_party.general_automation.presets import (
    GENERAL_AUTOMATION_PRESET_IDS,
    general_automation_preset_catalog,
    get_general_automation_preset,
)


PYTHON_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = (
    PYTHON_ROOT
    / "magi_agent"
    / "recipes"
    / "first_party"
    / "general_automation"
)


def test_general_automation_presets_compile_expected_roles_and_permission_ceilings() -> None:
    catalog = general_automation_preset_catalog()
    projections = {item.role_id: item for item in compile_general_automation_presets()}

    assert tuple(preset.role_id for preset in catalog) == GENERAL_AUTOMATION_PRESET_IDS
    assert set(projections) == set(GENERAL_AUTOMATION_PRESET_IDS)
    assert projections["automation.plan"].allowed_permissions == ("read", "meta")
    assert projections["automation.research"].allowed_permissions == ("read", "net", "meta")
    assert projections["automation.files"].allowed_permissions == ("read", "write", "meta")
    assert projections["automation.office"].allowed_permissions == ("read", "write", "meta")
    assert projections["automation.browser-inspect"].tool_categories == (
        "browser_open",
        "browser_snapshot",
        "browser_scrape",
    )
    assert projections["automation.browser-act"].approval_required_actions == (
        "click",
        "fill",
        "download",
        "submit",
    )
    assert projections["automation.scout"].allowed_permissions == ("read", "net", "meta")

    for projection in projections.values():
        assert projection.recipe_owned is True
        assert projection.core_owned is False
        assert projection.spawns_child_runners is False
        assert projection.adk_agent_role["roleId"] == projection.role_id


def test_mutation_tools_cannot_leak_into_plan_research_or_scout() -> None:
    for role_id in ("automation.plan", "automation.research", "automation.scout"):
        projection = project_general_automation_preset(role_id)

        assert set(projection.tool_categories).isdisjoint(MUTATION_TOOL_CATEGORIES)
        assert "write" not in projection.allowed_permissions
        assert "execute" not in projection.allowed_permissions
        assert projection.approval_required_actions == ()


def test_browser_submit_and_download_cannot_be_enabled_by_alias_metadata() -> None:
    projection = project_general_automation_preset(
        "automation.browser-inspect",
        alias_metadata={
            "toolCategories": ("browser_download", "browser_submit"),
            "enabledBrowserActions": ("download", "submit"),
        },
    )

    assert projection.tool_categories == (
        "browser_open",
        "browser_snapshot",
        "browser_scrape",
    )
    assert projection.enabled_browser_actions == ()
    assert projection.approval_required_actions == ()
    assert projection.alias_ignored_reason_codes == (
        "alias_browser_action_escalation_ignored",
        "alias_tool_category_escalation_ignored",
    )


def test_browser_act_keeps_mutating_browser_actions_approval_required_not_enabled() -> None:
    projection = project_general_automation_preset("automation.browser-act")

    assert projection.enabled_browser_actions == ()
    assert projection.approval_required_actions == ("click", "fill", "download", "submit")
    assert set(projection.tool_categories) >= {"browser_click", "browser_fill"}
    assert projection.public_projection()["authorityFlags"] == {
        "runnerSpawned": False,
        "liveToolsAttached": False,
        "browserSessionStarted": False,
        "productionRouteAttached": False,
    }


def test_preset_lookup_is_metadata_only_and_rejects_unknown_roles() -> None:
    preset = get_general_automation_preset("automation.office")

    assert preset.role_id == "automation.office"
    assert preset.adk_agent_role_metadata["adkPrimitive"] == "Agent role metadata"

    try:
        get_general_automation_preset("automation.unknown")
    except KeyError as exc:
        assert "automation.unknown" in str(exc)
    else:
        raise AssertionError("unknown preset lookup must fail")


def test_general_automation_preset_modules_do_not_spawn_or_import_core_runtime() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE_DIR.glob("*.py"))

    forbidden_fragments = (
        "magi_agent.adk_bridge",
        "magi_agent.runtime",
        "magi_agent.tools.dispatcher",
        "magi_agent.tools.registry",
        "google.adk.runners",
        "Runner(",
        "subprocess",
        "spawn(",
        "start_browser",
        "browser session",
    )
    for fragment in forbidden_fragments:
        assert fragment not in source
