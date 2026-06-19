"""Meta-test (C-5): forbid re-forking the canonical content-addressing kernel
and the frozen-contract base.

After consolidation, the single canonical-JSON -> sha256 primitive is
``magi_agent.ops.safety.canonical_digest`` and the single frozen-contract base is
``magi_agent.ops.authority.FrozenContractModel`` (re-exported from ops.safety).

The tree still contains ~70 file-local canonical-JSON->sha256 helpers (text
hashers, envelope-wrapping digesters, ``_digest_json`` variants) and many
re-pasted frozen ``ConfigDict`` trios that have NOT yet been migrated in this
pass. They are held in documented, SHRINKING allowlists so this ratchet stays
green while forbidding any NEW fork. Drive the allowlists toward empty in
follow-up sweeps.

This PR (P1.4) migrated the 7 byte-identical ``_digest_payload`` helpers
(connectors x3, billing/quota, billing/spend_guard, tenancy/context,
security/compliance) onto ``canonical_digest``, and collapsed 3 frozen-contract
bases (billing ``_BillingModel``/``_SpendModel``, tenancy ``_TenancyModel``)
onto ``FrozenContractModel``. Those files are intentionally ABSENT from the
allowlists below -- if they reappear as a fork, the ratchet fails.
"""

from __future__ import annotations

import ast
from pathlib import Path

_MAGI_ROOT = Path(__file__).resolve().parents[2] / "magi_agent"

# The canonical homes. Defining the primitive here is required, not forbidden.
_KERNEL_FILES = {
    "ops/safety.py",
    "ops/authority.py",
}

# Helpers that genuinely hash text (str input) rather than a mapping payload are
# out of scope for canonical_digest (which takes a Mapping). They are not forks.
_TEXT_HASHER_NAMES = {"_digest_text", "sha256_ref"}

# SHRINKING ALLOWLIST of un-migrated canonical-JSON->sha256 helper FILES (C-5
# follow-up sweep). These contain at least one function that does
# json.dumps(sort_keys=True, ...) + sha256. Most are envelope wrappers,
# `_digest_json` variants, or text/structure hashers whose per-site parity must
# be proven individually before migration. Two (missions/receipts,
# runtime/receipt_utils) are public re-export shims whose canonical_digest will
# delegate to the kernel in a follow-up. Goal: empty this set.
_DIGEST_FORK_ALLOWLIST: frozenset[str] = frozenset(
    {
        "adk_bridge/session_service.py",
        "artifacts/delivery_receipts.py",
        "artifacts/render_verification.py",
        "coding/repair_loop.py",
        "evidence/code_diagnostics_receipts.py",
        "evidence/coding_tool_receipts.py",
        "evidence/edit_match_receipts.py",
        "evidence/first_party_activity.py",
        "evidence/ledger_semantics.py",
        "evidence/tool_boundary.py",
        "gates/_bounded_pipe.py",
        "gates/api_canary_ladder.py",
        "gates/gate1a_readonly_tools.py",
        "gates/gate5b_full_toolhost.py",
        "harness/approval_receipts.py",
        "harness/scheduler_executor.py",
        "harness/scheduler_job_execution.py",
        "memory/projection.py",
        "meta_orchestration/child_roles.py",
        "meta_orchestration/commit_adapter.py",
        "meta_orchestration/event_projection.py",
        "meta_orchestration/final_assembly.py",
        # _digest_payload omits default=str + allow_nan; needs per-site parity proof
        "meta_orchestration/projection.py",
        "missions/lifecycle.py",
        # public re-export shim: canonical_digest -> ops.safety (follow-up)
        "missions/receipts.py",
        # _digest_payload kept (de-facto fine), but ops/job_queue also has _digest_text;
        # the allow_nan fix already landed in #695. Migration is a follow-up batch.
        "ops/job_queue.py",
        "ops/metrics.py",
        "permissions/auto_control.py",
        "plugins/mcp_adapter.py",
        "plugins/native/_common.py",
        "recipes/compiler.py",
        "recipes/composition.py",
        "recipes/effective_contract.py",
        "recipes/first_party/memory_recall.py",
        "recipes/hook_composition.py",
        "recipes/merge_algebra.py",
        "runtime/activity_boundary.py",
        "runtime/adk_turn_runner.py",
        "runtime/admission.py",
        "runtime/cache_safe_params.py",
        "runtime/context_lifecycle.py",
        "runtime/context_projection.py",
        "runtime/events.py",
        # _digest_payload omits default=str; needs per-site parity proof
        "runtime/heartbeat_contract.py",
        "runtime/policy_snapshot.py",
        "runtime/prompt_snapshot.py",
        "runtime/provider_receipts.py",
        # public re-export shim: canonical_digest -> ops.safety (follow-up)
        "runtime/receipt_utils.py",
        "runtime/request_shape.py",
        "runtime/resume_decision.py",
        "runtime/session_continuity_proof.py",
        "runtime/work_console_snapshot.py",
        "sandbox/policy.py",
        "security/advisory.py",
        "security/context_guard.py",
        "security/credentials.py",
        "shadow/gate2_activation_loop_a.py",
        "shadow/gate2_recipe_profile_resolver.py",
        "shadow/gate2_shadow_tool_policy.py",
        "shadow/gate5b4c3_shadow_comparison.py",
        # content_digest_for_payload wraps a schema envelope; envelope-fold follow-up
        "storage/content_addressed.py",
        "storage/durable_store.py",
        "storage/sqlite_store.py",
        "tools/core_toolhost.py",
        "tools/event_projection.py",
        "tools/local_readonly.py",
        "tools/spreadsheet_tools.py",
        # _digest_payload has a _ZERO_DIGEST empty-payload shortcut; follow-up batch
        "transport/product_admin.py",
        "web_acquisition/reference_research_tools.py",
        "workspace/sandbox_mutation.py",
    }
)


def _iter_modules() -> list[Path]:
    return sorted(
        path
        for path in _MAGI_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts and "test" not in path.relative_to(_MAGI_ROOT).as_posix()
    )


def _function_is_canonical_json_sha256(node: ast.FunctionDef) -> bool:
    """True if the function body does json.dumps(sort_keys=True) AND sha256/sha256_ref."""
    has_canonical_dumps = False
    has_sha256 = False
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Attribute) and func.attr == "dumps":
                if any(
                    kw.arg == "sort_keys"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                    for kw in sub.keywords
                ):
                    has_canonical_dumps = True
            if isinstance(func, ast.Attribute) and func.attr == "sha256":
                has_sha256 = True
            if isinstance(func, ast.Name) and func.id == "sha256_ref":
                has_sha256 = True
    return has_canonical_dumps and has_sha256


def _files_with_canonical_json_sha256() -> set[str]:
    found: set[str] = set()
    for path in _iter_modules():
        rel = path.relative_to(_MAGI_ROOT).as_posix()
        if rel in _KERNEL_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name in _TEXT_HASHER_NAMES:
                continue
            if _function_is_canonical_json_sha256(node):
                found.add(rel)
                break
    return found


def test_no_new_forked_canonical_digest() -> None:
    """Ratchet: no canonical-JSON->sha256 helper may appear outside the kernel
    that is not on the shrinking allowlist."""
    offenders = sorted(_files_with_canonical_json_sha256() - _DIGEST_FORK_ALLOWLIST)
    assert not offenders, (
        "New forked canonical-JSON->sha256 helper(s) found outside ops/safety.py. "
        "Re-point to magi_agent.ops.safety.canonical_digest, or (only if genuinely "
        "tricky) add a justified entry to _DIGEST_FORK_ALLOWLIST.\n"
        + "\n".join(offenders)
    )


def test_digest_fork_allowlist_is_shrinking() -> None:
    """Ratchet hygiene: every allowlist entry must (a) still exist and (b) still
    contain a fork, so migrated/removed sites are pruned from the allowlist and
    cannot silently mask a re-fork."""
    found = _files_with_canonical_json_sha256()
    stale = sorted(
        rel
        for rel in _DIGEST_FORK_ALLOWLIST
        if not (_MAGI_ROOT / rel).exists() or rel not in found
    )
    assert not stale, (
        "Stale digest-fork allowlist entries (file gone or no longer a fork -- "
        "remove them from _DIGEST_FORK_ALLOWLIST):\n" + "\n".join(stale)
    )


# ---- FrozenContractModel ratchet --------------------------------------------

# SHRINKING ALLOWLIST of files that still hand-paste the escape-hatch-disabling
# trio (a `model_construct` override raising "model_construct is disabled") rather
# than subclassing magi_agent.ops.authority.FrozenContractModel. Many of these
# also carry force-false serializers (the C-4 FalseOnlyAuthorityModel concern, a
# separate consolidation) or class-specific error-message strings whose parity
# must be preserved per-site -- so they are deferred. This PR collapsed the three
# clean generic-message bases (billing _BillingModel/_SpendModel, tenancy
# _TenancyModel); they are intentionally ABSENT below.
_FROZEN_BASE_FORK_ALLOWLIST: frozenset[str] = frozenset(
    {
        "coding/meta_adapter.py",
        # digest migrated this PR; force-false base deferred (C-4)
        "connectors/credential_lease.py",
        "connectors/registry.py",
        "evidence/child_runtime_envelope.py",
        "evidence/runtime_issuance.py",
        "meta_orchestration/child_acceptance.py",
        "meta_orchestration/child_roles.py",
        "meta_orchestration/commit_adapter.py",
        "meta_orchestration/final_assembly.py",
        "meta_orchestration/inspection_loop.py",
        "meta_orchestration/projection.py",
        "meta_orchestration/task_plan.py",
        "ops/job_queue.py",
        "ops/metrics.py",
        "packs/types.py",
        "permissions/auto_control.py",
        "research/acceptance_criteria.py",
        "research/action_claims.py",
        "research/boundary_enforcement.py",
        "research/child_roles.py",
        "research/claim_graph.py",
        "research/evidence_graph.py",
        "research/final_projection_gate.py",
        "research/meta_adapter.py",
        "research/output_contract_gate.py",
        "research/policy_pack.py",
        "research/repair.py",
        "research/source_proof.py",
        "runtime/heartbeat_contract.py",
        "sandbox/policy.py",
        # class-specific error-message strings; parity-preserve per-site (deferred)
        "security/compliance.py",
        "telemetry/deterministic_events.py",
        "web_acquisition/repo_research_tools.py",
    }
)


def _files_with_disabled_model_construct() -> set[str]:
    """Files containing the verbatim escape-hatch marker (a function that raises
    a 'model_construct is disabled' ValueError)."""
    found: set[str] = set()
    needle = "model_construct is disabled"
    for path in _iter_modules():
        rel = path.relative_to(_MAGI_ROOT).as_posix()
        if rel in _KERNEL_FILES:
            continue
        if needle in path.read_text(encoding="utf-8"):
            found.add(rel)
    return found


def test_no_new_forked_frozen_contract_base() -> None:
    """Ratchet: no new hand-pasted escape-hatch-disabling base outside
    ops/authority.py (subclass FrozenContractModel instead)."""
    offenders = sorted(_files_with_disabled_model_construct() - _FROZEN_BASE_FORK_ALLOWLIST)
    assert not offenders, (
        "New hand-written escape-hatch-disabling model base found. Subclass "
        "magi_agent.ops.authority.FrozenContractModel, or (only if genuinely "
        "tricky) add a justified entry to _FROZEN_BASE_FORK_ALLOWLIST.\n"
        + "\n".join(offenders)
    )


def test_frozen_base_allowlist_is_shrinking() -> None:
    found = _files_with_disabled_model_construct()
    stale = sorted(
        rel
        for rel in _FROZEN_BASE_FORK_ALLOWLIST
        if not (_MAGI_ROOT / rel).exists() or rel not in found
    )
    assert not stale, (
        "Stale frozen-base allowlist entries (file gone or no longer forks -- "
        "remove from _FROZEN_BASE_FORK_ALLOWLIST):\n" + "\n".join(stale)
    )


def test_no_second_module_level_canonical_digest_def() -> None:
    """Only the kernel + the two documented re-export shims may define a
    module-level `def canonical_digest`."""
    shims = {"missions/receipts.py", "runtime/receipt_utils.py"}
    offenders: list[str] = []
    for path in _iter_modules():
        rel = path.relative_to(_MAGI_ROOT).as_posix()
        if rel in _KERNEL_FILES or rel in shims:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "canonical_digest":
                offenders.append(rel)
    assert not offenders, (
        "Unexpected module-level `def canonical_digest` outside the kernel/shims: "
        + ", ".join(offenders)
    )
