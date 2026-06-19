"""Deletion guard (R2): no dynamic / string-literal reference to a deleted module.

The cardinal risk of a deletion PR is removing something referenced via a path
that a plain ``import``-statement scan misses: ``importlib.import_module(...)``,
``__import__(...)``, ``importlib.util.find_spec(...)``, an entry-point string, or
a bare string literal used as a module path. This meta-test scans every
non-test ``magi_agent`` source file for the *deleted module dotted paths as
string literals* and fails if any appears.

It also asserts the deep-research CARVE-OUTS remain importable, so a naive
``rg deep_research``-then-delete can never silently take them. (Note: in this
worktree the ``deep_research`` orchestrator + ``query_planner`` were KEPT, not
deleted, because they have live consumers — ``benchmarks/gaia/web_tools.py`` and
``tests/test_conservative_orchestration_defaults.py`` — so they are not in the
deletion set at all. These carve-out assertions still document the boundary.):

* ``magi_agent.web_acquisition.deep_research_config`` (live importers:
  ``cross_verifier.py`` / ``query_planner.py``).
* ``recipes.workflow_recipe.build_deep_research_workflow`` (a separate recipe
  builder, name-only reference to the concept).
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
from pathlib import Path


MAGI_AGENT_DIR = Path(__file__).resolve().parents[2] / "magi_agent"

# The exact deleted module dotted-paths whose appearance as a *string literal*
# would indicate a dynamic import the static scan in
# test_deletion_set_unimported.py cannot see.
DELETED_MODULE_PATHS = (
    "magi_agent.shadow.adk_eval_fixture_contract",
    "magi_agent.shadow.agent_methodology_contract",
    "magi_agent.shadow.artifact_channel_delivery_contract",
    "magi_agent.shadow.coding_child_conflict_resolution_contract",
    "magi_agent.shadow.coding_verification_evidence_contract",
    "magi_agent.shadow.control_projection_contract",
    "magi_agent.shadow.delegated_workflow_evidence_contract",
    "magi_agent.shadow.legal_academic_citation_detector_contract",
    "magi_agent.shadow.memory_source_authority_contract",
    "magi_agent.shadow.mission_lifecycle_contract",
    "magi_agent.shadow.mission_operator_goaljudge_contract",
    "magi_agent.shadow.office_automation_contract",
    "magi_agent.shadow.office_recipe_tool_alias_contract",
    "magi_agent.shadow.opencode_delta_contract",
    "magi_agent.shadow.patch_file_policy_contract",
    "magi_agent.shadow.path_shell_policy_contract",
    "magi_agent.shadow.permission_arbiter_contract",
    "magi_agent.shadow.research_source_evidence_contract",
    "magi_agent.shadow.toolhost_contract",
    "magi_agent.shadow.web_acquisition_browser_provider_contract",
    "magi_agent.rules.intent_classifier",
    "magi_agent.egress_proxy.evidence",
)

# Carve-outs: confusable names that MUST survive the deletion.
CARVE_OUT_MODULES = (
    "magi_agent.web_acquisition.deep_research_config",
)


def _string_literals(tree: ast.AST) -> list[str]:
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_no_string_literal_references_a_deleted_module() -> None:
    """No non-test magi_agent source uses a deleted module path as a string."""
    deleted = set(DELETED_MODULE_PATHS)
    offenders: list[str] = []
    for path in MAGI_AGENT_DIR.rglob("*.py"):
        if "tests" in path.relative_to(MAGI_AGENT_DIR).parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - defensive
            continue
        for literal in _string_literals(tree):
            for dotted in deleted:
                # Match the exact dotted path or a submodule string.
                if literal == dotted or literal.startswith(f"{dotted}."):
                    offenders.append(f"{path.relative_to(MAGI_AGENT_DIR)} -> {literal!r}")
    assert not offenders, (
        "Found string-literal references to P2.5-deleted modules (possible dynamic "
        "import / entry-point that a plain import scan misses):\n"
        + "\n".join(sorted(offenders))
    )


def test_carve_out_modules_remain_importable() -> None:
    """The confusable carve-out modules survive the deep_research deletion."""
    for dotted in CARVE_OUT_MODULES:
        assert (
            importlib.util.find_spec(dotted) is not None
        ), f"Carve-out {dotted} must remain importable (it has live importers)."


def test_build_deep_research_workflow_carve_out_remains_importable() -> None:
    """The separate recipe builder (name-only deep-research reference) survives."""
    module = importlib.import_module("magi_agent.recipes.workflow_recipe")
    assert hasattr(module, "build_deep_research_workflow"), (
        "recipes.workflow_recipe.build_deep_research_workflow is a separate recipe "
        "builder and must NOT be removed by the deep_research orchestrator deletion."
    )
