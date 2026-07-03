"""Same-object re-export guard for the PR-G2 engine routing extraction.

PR-G2 pure-moves the runner routing / policy-assembly concern out of the engine
driver into ``magi_agent.engine.engine_routing``. The driver re-imports every
moved name so existing import paths keep working and ``is`` identity (notably
the module-level sentinels compared with ``is``) is preserved. This test
freezes that: for every moved symbol, the driver attribute is the SAME object
as the new module's attribute.
"""

from __future__ import annotations

import importlib

import pytest

MOVED_SYMBOLS = [
    "RunnerPolicyAssembly",
    "compile_intent_bindings",
    "_select_policy_phase",
    "_classify_policy_phase_with_softening",
    "_Sentinel",
    "_EXHAUSTED",
    "_CANCELLED",
    "_MISSING",
    "_GateAttachment",
    "_RunnerRouteAttachment",
    "_restore_attr",
    "_str_tuple",
    "_routing_field",
    "_phase_routes",
    "_local_tool_names_for_route",
    "_non_empty_str",
    "_authority_safe_attachment_flags",
    "_runner_policy_routing_enabled",
    "_runner_policy_route_blocking_enabled",
    "_recipe_intent_binding_enabled",
    "_available_agent_tool_names",
    "_tool_name",
    "_tool_names_for_intent",
    "_CODING_PROMPT_MARKERS",
    "_CODING_PHASES",
    "_LOCAL_READONLY_TOOL_NAMES",
]


@pytest.mark.parametrize("name", MOVED_SYMBOLS)
def test_driver_reexports_same_object(name: str) -> None:
    driver = importlib.import_module("magi_agent.engine.driver")
    routing = importlib.import_module("magi_agent.engine.engine_routing")
    assert getattr(driver, name) is getattr(routing, name)


def test_legacy_cli_engine_import_path_still_works() -> None:
    from magi_agent.cli.engine import RunnerPolicyAssembly as via_cli
    from magi_agent.engine.engine_routing import RunnerPolicyAssembly as via_new

    assert via_cli is via_new


def test_sentinels_preserve_identity_for_is_comparison() -> None:
    driver = importlib.import_module("magi_agent.engine.driver")
    routing = importlib.import_module("magi_agent.engine.engine_routing")
    assert driver._EXHAUSTED is routing._EXHAUSTED
    assert driver._CANCELLED is routing._CANCELLED
    assert driver._MISSING is routing._MISSING
    # Distinct sentinels stay distinct.
    assert driver._EXHAUSTED is not driver._CANCELLED


def test_classify_still_reaches_task_type_helpers() -> None:
    # The routing classifier reaches _extract_task_types / _normalize_task_type
    # lazily from the driver; exercise a coding-prompt classification to prove
    # the cross-module resolution works at call time (no import cycle).
    from magi_agent.engine.engine_routing import (
        RunnerPolicyAssembly,
        _classify_policy_phase_with_softening,
    )

    assembly = RunnerPolicyAssembly()
    phase, softened = _classify_policy_phase_with_softening(
        phases=("patch_generation", "final_answer_drafting"),
        prompt="please fix this bug in the code",
        harness_state=None,
        assembly=assembly,
    )
    assert phase == "patch_generation"
    assert softened is None
