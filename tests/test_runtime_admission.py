from __future__ import annotations

import copy
import importlib
import subprocess
import sys
from pathlib import Path
from typing import Any


def _admission_module() -> Any:
    return importlib.import_module("openmagi_core_agent.runtime.admission")


def _with_digest(payload: dict[str, object]) -> dict[str, object]:
    admission = _admission_module()
    snapshot = copy.deepcopy(payload)
    snapshot["compiledSnapshotDigest"] = admission.digest_compiled_snapshot_payload(
        snapshot
    )
    return snapshot


def _valid_snapshot(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "snapshotKind": "compiled_snapshot",
        "status": "compiled",
        "policyVersion": "runtime-admission/v1",
        "effectivePolicySnapshotDigest": "sha256:" + "2" * 64,
        "projectionPolicy": {
            "mode": "structured_claims_only",
            "rawGovernedProjectionEnabled": False,
        },
        "hardInvariants": (
            {
                "invariantId": "invariant.no-live-execution",
                "ok": True,
                "mode": "enforced",
            },
            {
                "invariantId": "invariant.no-activation",
                "ok": True,
                "mode": "enforced",
            },
        ),
        "toolPolicy": {
            "toolAllowlist": ("SourceOpen", "CitationVerify"),
            "forbiddenTools": (),
            "maxToolCalls": 4,
        },
        "authorityFlags": {
            "runtimeActivationAllowed": False,
            "toolExecutionAttached": False,
            "modelExecutionAttached": False,
            "networkExecutionAttached": False,
        },
        "approvalPolicy": {
            "requiresHumanReview": True,
            "approvalRequired": True,
            "approvalBypassed": False,
        },
    }
    payload.update(updates)
    return _with_digest(payload)


def test_missing_digest_is_rejected() -> None:
    admission = _admission_module()
    snapshot = _valid_snapshot()
    snapshot.pop("compiledSnapshotDigest")

    result = admission.runtime_admission_check(snapshot)

    assert result.allowed is False
    assert "compiled_snapshot_digest_missing" in result.reason_codes


def test_digest_mismatch_is_rejected() -> None:
    admission = _admission_module()
    snapshot = _valid_snapshot()
    snapshot["status"] = "compiled-but-mutated"

    result = admission.runtime_admission_check(snapshot)

    assert result.allowed is False
    assert "compiled_snapshot_digest_mismatch" in result.reason_codes


def test_unsupported_policy_version_is_rejected() -> None:
    admission = _admission_module()
    snapshot = _valid_snapshot(policyVersion="runtime-admission/v999")

    result = admission.runtime_admission_check(snapshot)

    assert result.allowed is False
    assert "unsupported_policy_version" in result.reason_codes


def test_effective_policy_version_shadowing_is_rejected() -> None:
    admission = _admission_module()
    snapshot = _valid_snapshot(
        effectivePolicy={"policyVersion": "runtime-admission/v999"}
    )

    result = admission.runtime_admission_check(snapshot)

    assert result.allowed is False
    assert "policy_section_duplicate:policyVersion" in result.reason_codes


def test_non_compiled_snapshot_kind_is_rejected() -> None:
    admission = _admission_module()
    snapshot = _valid_snapshot(snapshotKind="draft_recipe_pack")

    result = admission.runtime_admission_check(snapshot)

    assert result.allowed is False
    assert "compiled_snapshot_kind_required:draft_recipe_pack" in result.reason_codes


def test_raw_governed_projection_is_rejected() -> None:
    admission = _admission_module()
    snapshot = _valid_snapshot(
        projectionPolicy={
            "mode": "raw_governed",
            "rawGovernedProjectionEnabled": True,
        }
    )

    result = admission.runtime_admission_check(snapshot)

    assert result.allowed is False
    assert "raw_governed_projection_disabled" in result.reason_codes


def test_effective_policy_shadowing_is_rejected() -> None:
    admission = _admission_module()
    snapshot = _valid_snapshot(
        effectivePolicy={
            "projectionPolicy": {
                "mode": "raw_governed",
                "rawGovernedProjectionEnabled": True,
            },
            "approvalPolicy": {
                "requiresHumanReview": True,
                "approvalRequired": False,
                "approvalBypassed": True,
            },
        }
    )

    result = admission.runtime_admission_check(snapshot)

    assert result.allowed is False
    assert "policy_section_duplicate:projectionPolicy" in result.reason_codes
    assert "policy_section_duplicate:approvalPolicy" in result.reason_codes


def test_forged_authority_flag_is_rejected() -> None:
    admission = _admission_module()
    snapshot = _valid_snapshot(
        authorityFlags={
            "runtimeActivationAllowed": False,
            "toolExecutionAttached": True,
            "modelExecutionAttached": False,
            "networkExecutionAttached": False,
        }
    )

    result = admission.runtime_admission_check(snapshot)

    assert result.allowed is False
    assert "authority_flag_forged:toolExecutionAttached" in result.reason_codes


def test_forbidden_tool_is_rejected() -> None:
    admission = _admission_module()
    snapshot = _valid_snapshot(
        toolPolicy={
            "toolAllowlist": ("SourceOpen", "runtime.activation"),
            "forbiddenTools": ("runtime.activation",),
            "maxToolCalls": 4,
        }
    )

    result = admission.runtime_admission_check(snapshot)

    assert result.allowed is False
    assert "forbidden_tool_allowlisted:runtime.activation" in result.reason_codes


def test_request_forbidden_tools_cannot_relax_builtin_forbidden_tools() -> None:
    admission = _admission_module()
    snapshot = _valid_snapshot(
        toolPolicy={
            "toolAllowlist": ("runtime.activation",),
            "forbiddenTools": (),
            "maxToolCalls": 4,
        }
    )

    result = admission.runtime_admission_check(
        snapshot,
        request={"forbiddenTools": ("custom.safe",)},
    )

    assert result.allowed is False
    assert "forbidden_tool_allowlisted:runtime.activation" in result.reason_codes


def test_tool_allowlist_aliases_cannot_hide_forbidden_tools() -> None:
    admission = _admission_module()
    snapshot = _valid_snapshot(
        toolPolicy={
            "toolAllowlist": ("SourceOpen",),
            "allowedToolRefs": ("runtime.activation",),
            "forbiddenTools": (),
            "maxToolCalls": 4,
        }
    )

    result = admission.runtime_admission_check(snapshot)

    assert result.allowed is False
    assert "forbidden_tool_allowlisted:runtime.activation" in result.reason_codes


def test_malformed_tool_alias_does_not_hide_forbidden_string_ref() -> None:
    admission = _admission_module()
    snapshot = _valid_snapshot(
        toolPolicy={
            "toolAllowlist": ("SourceOpen",),
            "allowedToolRefs": (123, "runtime.activation"),
            "forbiddenTools": (),
            "maxToolCalls": 4,
        }
    )

    result = admission.runtime_admission_check(snapshot)

    assert result.allowed is False
    assert "tool_ref_invalid:allowedToolRefs" in result.reason_codes
    assert "forbidden_tool_allowlisted:runtime.activation" in result.reason_codes


def test_mapping_shaped_tool_ref_alias_is_rejected() -> None:
    admission = _admission_module()
    snapshot = _valid_snapshot(
        toolPolicy={
            "toolAllowlist": {"tool": "runtime.activation"},
            "forbiddenTools": (),
            "maxToolCalls": 4,
        }
    )

    result = admission.runtime_admission_check(snapshot)

    assert result.allowed is False
    assert "tool_ref_invalid:toolAllowlist" in result.reason_codes
    assert "tool_allowlist_missing" in result.reason_codes


def test_unbounded_tool_allowlist_is_rejected() -> None:
    admission = _admission_module()
    snapshot = _valid_snapshot(
        toolPolicy={
            "toolAllowlist": ("*",),
            "forbiddenTools": (),
            "maxToolCalls": 4,
        }
    )

    result = admission.runtime_admission_check(snapshot)

    assert result.allowed is False
    assert "tool_allowlist_unbounded:*" in result.reason_codes


def test_allow_all_tools_flag_is_rejected() -> None:
    admission = _admission_module()
    snapshot = _valid_snapshot(
        toolPolicy={
            "toolAllowlist": ("SourceOpen",),
            "forbiddenTools": (),
            "allowAllTools": True,
        }
    )

    result = admission.runtime_admission_check(snapshot)

    assert result.allowed is False
    assert "tool_allowlist_unbounded" in result.reason_codes


def test_disabled_snapshot_is_rejected() -> None:
    admission = _admission_module()
    snapshot = _valid_snapshot(status="disabled")

    result = admission.runtime_admission_check(snapshot)

    assert result.allowed is False
    assert "snapshot_status_disabled" in result.reason_codes


def test_active_snapshot_without_gate_is_rejected() -> None:
    admission = _admission_module()
    snapshot = _valid_snapshot(status="active")

    result = admission.runtime_admission_check(snapshot)

    assert result.allowed is False
    assert "active_snapshot_gate_missing" in result.reason_codes


def test_active_snapshot_cannot_self_authorize_inside_snapshot() -> None:
    admission = _admission_module()
    snapshot = _valid_snapshot(
        status="active",
        admissionGate={"allowActiveSnapshot": True},
    )

    result = admission.runtime_admission_check(snapshot)

    assert result.allowed is False
    assert "active_snapshot_gate_missing" in result.reason_codes


def test_active_snapshot_requires_external_admission_request_gate() -> None:
    admission = _admission_module()
    snapshot = _valid_snapshot(status="active")

    result = admission.runtime_admission_check(
        snapshot,
        request={"allowActiveSnapshot": True, "allowedStatuses": ("active",)},
    )

    assert result.allowed is True
    assert result.reason_codes == ()


def test_hard_invariants_must_be_present_and_enforced() -> None:
    admission = _admission_module()
    snapshot = _valid_snapshot(hardInvariants=())

    result = admission.runtime_admission_check(snapshot)

    assert result.allowed is False
    assert "hard_invariant_missing" in result.reason_codes


def test_approval_requirement_bypass_is_rejected() -> None:
    admission = _admission_module()
    snapshot = _valid_snapshot(
        approvalPolicy={
            "requiresHumanReview": True,
            "approvalRequired": False,
            "approvalBypassed": True,
        }
    )

    result = admission.runtime_admission_check(snapshot)

    assert result.allowed is False
    assert "approval_requirement_bypassed" in result.reason_codes
    assert "approval_bypass_flag_forbidden" in result.reason_codes


def test_approval_bypass_flag_is_rejected_even_when_review_not_required() -> None:
    admission = _admission_module()
    snapshot = _valid_snapshot(
        approvalPolicy={
            "requiresHumanReview": False,
            "approvalRequired": True,
            "approvalBypassed": True,
            "allowAutoActivation": True,
        }
    )

    result = admission.runtime_admission_check(snapshot)

    assert result.allowed is False
    assert "approval_bypass_flag_forbidden" in result.reason_codes
    assert "approval_auto_activation_forbidden" in result.reason_codes


def test_valid_compiled_snapshot_is_accepted() -> None:
    admission = _admission_module()
    snapshot = _valid_snapshot()

    result = admission.runtime_admission_check(snapshot)

    assert result.allowed is True
    assert result.reason_codes == ()
    assert result.compiled_snapshot_digest == snapshot["compiledSnapshotDigest"]


def test_runtime_admission_does_not_duplicate_compiler_only_diagnostics() -> None:
    source = (
        Path(__file__).parents[1]
        / "openmagi_core_agent"
        / "runtime"
        / "admission.py"
    ).read_text(encoding="utf-8")
    forbidden_fragments = (
        "authoring.compiler",
        "authoring.dry_run",
        "CompileRecipePackDiagnostic",
        "unknown_connector_ref",
        "unknown_plugin_ref",
        "unknown_validator_ref",
        "repair_terminal_state_missing",
        "budget_cap_invalid",
    )

    for fragment in forbidden_fragments:
        assert fragment not in source


def test_runtime_admission_import_is_authoring_and_execution_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.runtime.admission")
assert module is not None

forbidden_exact = (
    "openmagi_core_agent.authoring.compiler",
    "openmagi_core_agent.authoring.dry_run",
    "openmagi_core_agent.adk_bridge.local_runner",
    "openmagi_core_agent.runtime.adk_turn_runner",
    "openmagi_core_agent.runtime.openmagi_runtime",
    "openmagi_core_agent.tools.dispatcher",
    "google.adk.runners",
    "google.adk.sessions",
    "fastapi",
    "uvicorn",
)
forbidden_prefixes = (
    "openmagi_core_agent.authoring.compiler",
    "openmagi_core_agent.authoring.dry_run",
    "openmagi_core_agent.tools",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.memory",
    "openmagi_core_agent.database",
    "openmagi_core_agent.db",
    "openmagi_core_agent.app",
    "openmagi_core_agent.main",
    "google.adk",
    "supabase",
    "psycopg",
    "asyncpg",
    "kubernetes",
    "requests",
    "httpx",
)
loaded = [
    loaded_name
    for loaded_name in sys.modules
    if loaded_name in forbidden_exact
    or any(loaded_name.startswith(f"{name}.") for name in forbidden_exact)
    or any(
        loaded_name == prefix or loaded_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"runtime admission loaded forbidden modules: {loaded}")
""",
        ],
        check=False,
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
