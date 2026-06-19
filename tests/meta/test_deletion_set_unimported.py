"""Deletion guard: no module in magi_agent statically imports a deleted module.

P2.5 (issues H-1/H-2/H-3/H-10) deleted a set of dead modules. This meta-test is
the permanent contract that none of them is re-imported via a normal Python
``import`` / ``from ... import ...`` statement anywhere in the package, and that
none of the deleted module files reappears on disk.

The carve-out names that share a confusable prefix with a deleted module
(``deep_research_config``, ``build_deep_research_workflow``) are asserted
importable in ``test_deletion_set_no_dynamic_ref.py`` so a naive name-grep can
never silently take them with the dead module.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest


MAGI_AGENT_DIR = Path(__file__).resolve().parents[2] / "magi_agent"

# Fully-qualified module paths removed by P2.5.
DELETED_MODULES = (
    # H-1: dead shadow TS-parity contracts (20)
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
    # H-3: LLM intent-classifier pre-pass (whole rules/ package)
    "magi_agent.rules.intent_classifier",
    "magi_agent.rules",
    # H-10: egress-proxy evidence producer (never called)
    "magi_agent.egress_proxy.evidence",
)


@pytest.mark.parametrize("dotted", DELETED_MODULES)
def test_deleted_module_is_not_importable(dotted: str) -> None:
    try:
        spec = importlib.util.find_spec(dotted)
    except ModuleNotFoundError:
        # Parent package itself was deleted (e.g. magi_agent.rules) — definitively gone.
        spec = None
    assert spec is None, f"{dotted} was deleted by P2.5 and must not be importable."


def _import_targets(tree: ast.AST) -> set[str]:
    """Collect dotted module names that appear in import statements."""
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                targets.add(node.module)
                for alias in node.names:
                    targets.add(f"{node.module}.{alias.name}")
    return targets


def test_no_module_statically_imports_a_deleted_module() -> None:
    """Parse every magi_agent module's AST; assert no import targets a deleted module."""
    deleted = set(DELETED_MODULES)
    offenders: list[str] = []
    for path in MAGI_AGENT_DIR.rglob("*.py"):
        if "tests" in path.relative_to(MAGI_AGENT_DIR).parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - defensive
            continue
        for target in _import_targets(tree):
            # Match the module itself or a `from <module> import X` form.
            if target in deleted or any(
                target == m or target.startswith(f"{m}.") for m in deleted
            ):
                offenders.append(f"{path.relative_to(MAGI_AGENT_DIR)} -> {target}")
    assert not offenders, (
        "Found static imports of P2.5-deleted modules:\n" + "\n".join(sorted(offenders))
    )
