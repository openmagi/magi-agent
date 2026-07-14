"""Deletion guard for the dead `shadow/*_contract.py` TS-parity scaffolding.

P2.5 (issue H-1) deleted the 20 `*_contract.py` files under
``magi_agent/shadow/`` that had zero importers outside their own tests and the
``shadow/__init__.py`` lazy re-export shim. The contracts with live consumers
stay. P5-M2 then deleted the two internal-endpoint contracts
(``gate5b4_internal_endpoint_contract``,
``gate5b4c2_shadow_invocation_contract``) once the fleet flip made their sole
consumers extinct: chat-proxy stopped calling the canary-era internal endpoints
at clawy C4 (#1812), the ``/v1/internal/gate5b/shadow-invocations`` route and
its ``transport/shadow_invocations.py`` handler were removed, and the two names
were dropped from the gate5 readiness surface table. This guard encodes that
contract permanently:

1. The deleted contracts must stay gone (no module file, not importable).
2. The kept contracts must still be importable (they are on the live serving /
   readiness / grounded-answer paths).
3. No new ``*_contract.py`` may reappear in ``shadow/`` without a live importer
   outside ``shadow/`` and ``tests/`` (prevents the dead-scaffolding pattern from
   silently regrowing).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SHADOW_DIR = Path(__file__).resolve().parents[2] / "magi_agent" / "shadow"
MAGI_AGENT_DIR = SHADOW_DIR.parent

# The 20 dead TS-parity contracts deleted by P2.5 H-1, plus the two
# internal-endpoint contracts deleted by P5-M2 (caller extinct after clawy C4).
DELETED_SHADOW_CONTRACTS = (
    "adk_eval_fixture_contract",
    "agent_methodology_contract",
    "artifact_channel_delivery_contract",
    "coding_child_conflict_resolution_contract",
    "coding_verification_evidence_contract",
    "control_projection_contract",
    "delegated_workflow_evidence_contract",
    "gate5b4_internal_endpoint_contract",
    "gate5b4c2_shadow_invocation_contract",
    "legal_academic_citation_detector_contract",
    "memory_source_authority_contract",
    "mission_lifecycle_contract",
    "mission_operator_goaljudge_contract",
    "office_automation_contract",
    "office_recipe_tool_alias_contract",
    "opencode_delta_contract",
    "patch_file_policy_contract",
    "path_shell_policy_contract",
    "permission_arbiter_contract",
    "research_source_evidence_contract",
    "toolhost_contract",
    "web_acquisition_browser_provider_contract",
)

# The contracts kept because they have live consumers outside shadow/+tests/.
KEPT_SHADOW_CONTRACTS = (
    "fact_grounding_verifier_contract",
    "gate5b4c3_shadow_generation_contract",
    "workspace_adoption_preflight_contract",
)


@pytest.mark.parametrize("name", DELETED_SHADOW_CONTRACTS)
def test_deleted_shadow_contract_file_is_gone(name: str) -> None:
    assert not (SHADOW_DIR / f"{name}.py").exists(), (
        f"shadow/{name}.py was deleted by P2.5 H-1 (dead TS-parity contract); "
        "it must not reappear."
    )


@pytest.mark.parametrize("name", DELETED_SHADOW_CONTRACTS)
def test_deleted_shadow_contract_is_not_importable(name: str) -> None:
    assert (
        importlib.util.find_spec(f"magi_agent.shadow.{name}") is None
    ), f"magi_agent.shadow.{name} must not be importable after P2.5 H-1 deletion."


@pytest.mark.parametrize("name", KEPT_SHADOW_CONTRACTS)
def test_kept_shadow_contract_file_exists(name: str) -> None:
    assert (SHADOW_DIR / f"{name}.py").exists(), (
        f"shadow/{name}.py has live consumers and must NOT be deleted."
    )


@pytest.mark.parametrize("name", KEPT_SHADOW_CONTRACTS)
def test_kept_shadow_contract_is_importable(name: str) -> None:
    assert (
        importlib.util.find_spec(f"magi_agent.shadow.{name}") is not None
    ), f"magi_agent.shadow.{name} is live and must stay importable."


def _python_importers(name: str) -> list[str]:
    """Files under magi_agent/ that mention *name* outside shadow/ and tests/."""
    token = name
    hits: list[str] = []
    for path in MAGI_AGENT_DIR.rglob("*.py"):
        rel = path.relative_to(MAGI_AGENT_DIR)
        parts = rel.parts
        if parts[0] == "shadow":
            continue
        if "tests" in parts:
            continue
        if token in path.read_text(encoding="utf-8"):
            hits.append(str(rel))
    return hits


def test_every_remaining_shadow_contract_has_a_live_consumer() -> None:
    """No `*_contract.py` may live in shadow/ without a live importer.

    This is the permanent guard against the dead-scaffolding pattern regrowing:
    every remaining ``*_contract.py`` must be referenced by at least one module
    outside ``shadow/`` and outside ``tests/``.
    """
    remaining = sorted(p.stem for p in SHADOW_DIR.glob("*_contract.py"))
    assert remaining == sorted(KEPT_SHADOW_CONTRACTS), (
        "Unexpected set of shadow/*_contract.py files. Expected only the 5 kept "
        f"contracts. Got: {remaining}"
    )
    for name in remaining:
        importers = _python_importers(name)
        assert importers, (
            f"shadow/{name}.py has no live consumer outside shadow/+tests/ — "
            "it is dead scaffolding and should be deleted (P2.5 H-1 pattern)."
        )
