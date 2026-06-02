from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.shadow.opencode_delta_contract import (
    OPENCODE_LATEST_COMMIT,
    REQUIRED_OPENCODE_DELTA_ROWS,
    OpenCodeDeltaMatrix,
    load_opencode_delta_matrix,
    project_opencode_delta_matrix,
)


FIXTURES = Path(__file__).parent / "fixtures" / "opencode_delta"


def test_opencode_delta_matrix_covers_required_rows_with_safe_invariants() -> None:
    matrix = load_opencode_delta_matrix("harness_delta_matrix.json", fixture_root=FIXTURES)
    projection = project_opencode_delta_matrix(matrix)

    assert matrix.schema_version == "opencodeHarnessDeltaMatrix.v1"
    assert projection.matrix_id == "opencode_harness_delta_pr0_20260526"
    assert projection.row_order == REQUIRED_OPENCODE_DELTA_ROWS
    assert projection.by_status == {
        "covered": 8,
        "delegated": 3,
        "missing": 3,
    }
    assert projection.no_live_authority is True
    assert projection.default_off is True
    assert projection.core_touch_allowed_count == 0
    assert set(projection.authority_flags.model_dump(by_alias=True).values()) == {False}

    rows = {row.row_id: row for row in matrix.rows}

    assert {row.opencode_commit for row in matrix.rows} == {OPENCODE_LATEST_COMMIT}
    assert all(
        row.opencode_source.startswith(f"anomalyco/opencode@{OPENCODE_LATEST_COMMIT}:")
        for row in matrix.rows
    )
    assert {row.row_id: row.planned_slice for row in matrix.rows} == {
        "provider_compatibility_fixtures": "PR-A",
        "snapshot_pre_stream_digest_boundary": "PR-B",
        "output_budgeting_artifact_contract": "PR-C",
        "auto_permission_receipts_no_behavior_drift": "PR-D",
        "shell_metadata_contract": "PR-E",
        "repetition_guard_contract": "PR-F",
        "runtime_plan_mode_capability": "PR-G",
        "edit_patch_fidelity_contract": "PR-H",
        "lsp_lifecycle_contract": "PR-I",
        "runtime_event_replay_fence": "PR-J",
        "todo_projection_contract": "PR-K",
        "client_protocol_boundary": "PR-L",
        "mcp_lifecycle_status_config": "PR-M",
        "provider_header_provenance_allowlist": "PR-M",
    }

    assert rows["snapshot_pre_stream_digest_boundary"].status == "covered"
    assert rows["snapshot_pre_stream_digest_boundary"].openmagi_target == (
        "magi_agent/runtime/provider_receipts.py",
        "magi_agent/runtime/policy_snapshot.py",
        "tests/test_live_provider_receipts.py",
    )

    assert rows["provider_compatibility_fixtures"].status == "missing"
    assert rows["provider_compatibility_fixtures"].planned_slice == "PR-A"
    assert rows["provider_compatibility_fixtures"].owning_layer == (
        "PR-A provider adapter/harness contract"
    )
    assert rows["provider_compatibility_fixtures"].openmagi_target == (
        "magi_agent/providers/provider_compat.py",
        "tests/fixtures/provider_compat/provider_cases.json",
        "tests/test_provider_compat.py",
        "docs/superpowers/plans/2026-05-20-python-adk-first-party-live-provider-quality-plan.md",
    )
    assert rows["provider_compatibility_fixtures"].activation_gate == (
        "PR-A-provider-compatibility-fixtures"
    )

    assert rows["output_budgeting_artifact_contract"].status == "covered"
    assert "tests/test_e2e_harness_pr3_tool_schema_output_store.py" in rows[
        "output_budgeting_artifact_contract"
    ].openmagi_target

    assert rows["auto_permission_receipts_no_behavior_drift"].status == "covered"
    assert rows["auto_permission_receipts_no_behavior_drift"].default_off is True
    assert rows["auto_permission_receipts_no_behavior_drift"].live_authority_allowed is False

    assert rows["runtime_plan_mode_capability"].status == "covered"
    assert rows["runtime_plan_mode_capability"].owning_layer == "Python ADK harness"

    assert rows["lsp_lifecycle_contract"].status == "missing"
    assert rows["lsp_lifecycle_contract"].owning_layer == "Future coding intelligence harness"

    assert rows["runtime_event_replay_fence"].status == "delegated"
    assert rows["runtime_event_replay_fence"].activation_gate == (
        "merged-gate-2-readiness-foundation"
    )
    assert "magi_agent/gates/gate2_readiness.py" in rows[
        "runtime_event_replay_fence"
    ].openmagi_target
    assert "tests/test_gate2_readiness_foundations.py" in rows[
        "runtime_event_replay_fence"
    ].openmagi_target
    assert "merged Gate 2 readiness foundation" in rows["runtime_event_replay_fence"].notes

    assert rows["provider_header_provenance_allowlist"].status == "missing"
    assert rows["provider_header_provenance_allowlist"].planned_slice == "PR-M"
    assert rows["provider_header_provenance_allowlist"].activation_gate == (
        "PR-M-provider-header-provenance-allowlist"
    )
    assert rows["repetition_guard_contract"].opencode_source.endswith(
        "packages/opencode/src/session/processor.ts"
    )
    assert rows["runtime_plan_mode_capability"].opencode_source.endswith(
        "packages/opencode/src/session/processor.ts"
    )


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload["rows"][0].update({"liveAuthorityAllowed": True}),
            id="live-authority",
        ),
        pytest.param(
            lambda payload: payload["rows"][0].update({"defaultOff": False}),
            id="default-on",
        ),
        pytest.param(
            lambda payload: payload["rows"][0].update({"coreTouchAllowed": True}),
            id="core-touch-without-reason",
        ),
        pytest.param(
            lambda payload: payload["rows"][1].update(
                {
                    "coreTouchAllowed": True,
                    "coreTouchReason": "concrete substrate gap blocks fixture-only validation",
                }
            ),
            id="core-touch-hard-deny-even-with-reason",
        ),
        pytest.param(
            lambda payload: payload["rows"][8].update(
                {
                    "coreTouchAllowed": True,
                    "coreTouchReason": "security invariant requires core gap report",
                }
            ),
            id="core-touch-hard-deny-missing-row",
        ),
        pytest.param(
            lambda payload: payload["rows"][0].update({"coreTouchReason": "unneeded"})
            if not payload["rows"][0]["coreTouchAllowed"]
            else None,
            id="core-reason-without-touch",
        ),
        pytest.param(
            lambda payload: payload["rows"][0].update(
                {"opencodeCommit": "external-source-research-delegated"}
            ),
            id="unpinned-opencode-commit",
        ),
        pytest.param(
            lambda payload: payload["rows"][0].update(
                {"opencodeSource": "OpenCode stream snapshot pending source verification"}
            ),
            id="unpinned-opencode-source",
        ),
        pytest.param(
            lambda payload: payload["rows"][0].update({"notes": "write raw output to /tmp/x"}),
            id="raw-tmp-output",
        ),
        pytest.param(
            lambda payload: payload["rows"][0].update(
                {"notes": "allow provider header safety by denylist only"}
            ),
            id="denylist-only-provider-safety",
        ),
        pytest.param(
            lambda payload: payload["rows"][1].update({"status": "planned"}),
            id="undecided-status",
        ),
    ),
)
def test_opencode_delta_matrix_rejects_authority_drift_and_unsafe_guidance(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "harness_delta_matrix.json").read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises(ValidationError):
        OpenCodeDeltaMatrix.model_validate(payload)


def test_opencode_delta_contract_import_boundary_stays_runtime_free() -> None:
    code = """
import sys
from pathlib import Path

from magi_agent.shadow.opencode_delta_contract import (
    load_opencode_delta_matrix,
    project_opencode_delta_matrix,
)

fixture_root = Path('tests/fixtures/opencode_delta')
matrix = load_opencode_delta_matrix('harness_delta_matrix.json', fixture_root=fixture_root)
project_opencode_delta_matrix(matrix)

forbidden = (
    'google.adk.runners',
    'magi_agent.adk_bridge.local_runner',
    'magi_agent.adk_bridge.runner_adapter',
    'magi_agent.adk_bridge.tool_adapter',
    'magi_agent.tools.dispatcher',
    'magi_agent.tools.registry',
    'magi_agent.plugins.manager',
    'magi_agent.plugins.native_catalog',
    'magi_agent.memory',
    'magi_agent.app',
    'magi_agent.transport.chat',
    'magi_agent.routes',
)
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise AssertionError(f'forbidden modules loaded: {loaded}')
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
