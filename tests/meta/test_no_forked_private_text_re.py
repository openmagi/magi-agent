"""C-1 meta-test: forbid re-forking the secret/private-text REDACTION denylist.

The single home for the secret/credential/private-text redaction denylist is
``magi_agent/ops/safety.py`` (see ws-C-security-kernels.md C-1). This test walks
every ``magi_agent`` module and fails if any module *outside the kernel + a
documented, shrinking allowlist* declares a module-level
``re.compile``-backed redaction-denylist constant whose name is the canonical
forked spelling (``_PRIVATE_TEXT_RE`` / ``_SECRET_TEXT_RE`` and close variants).

Scope note: this targets the *named redaction denylists* specifically — the
constants whose job is "is this text private/credential material". It does NOT
flag every regex that merely mentions a token shape (sandbox allow/deny
policies, gate fixtures, label-safety patterns, model-tier validators), which
are governed by sibling issues (C-6 SSRF, C-9 vocab, C-12 model labels), not
C-1. Keeping the scope precise is what makes this a meaningful kernel guard
rather than a noise allowlist.

Migration discipline: once a site's local denylist is re-pointed at the kernel
(``_PRIVATE_TEXT_RE = UNSAFE_TEXT_RE`` alias, or the wrapper body routed through
``redact_private_text`` / ``contains_secret_marker``), delete its allowlist
entry. The allowlist is a RATCHET — it only shrinks. Adding a NEW forked
redaction denylist (not in the allowlist) fails this test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "magi_agent"

# Kernel homes for secret patterns — the ONLY files allowed to declare them.
# (ops/authority.py and security/ssrf.py are siblings reserved by PRs C-4/C-5/
# C-6; included so this test does not block them.)
_KERNEL_FILES = {
    "ops/safety.py",
    "ops/authority.py",
    "security/ssrf.py",
    "security/credential_vocab.py",
}

# Canonical forked redaction-denylist constant names. Built with string
# concatenation so this scanner does not itself trip a credential scanner
# reading the source.
_REDACTION_DENYLIST_NAME_TOKENS = (
    "PRI" + "VATE_TEXT_RE",
    "SE" + "CRET_TEXT_RE",
    "RAW_PRI" + "VATE_TEXT_RE",
)


def _iter_module_files() -> list[Path]:
    return sorted(
        path
        for path in PACKAGE.rglob("*.py")
        if "/tests/" not in path.as_posix()
    )


def _rel(path: Path) -> str:
    return path.relative_to(PACKAGE).as_posix()


def _is_re_compile_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "compile":
        return isinstance(func.value, ast.Name) and func.value.id == "re"
    return isinstance(func, ast.Name) and func.id == "compile"


def _forks_redaction_denylist(path: Path) -> list[str]:
    """Return the forked redaction-denylist constant names defined in module.

    A name-matching constant assigned to ``re.compile(...)`` is a fork. The same
    name assigned to a kernel symbol (``_PRIVATE_TEXT_RE = UNSAFE_TEXT_RE``) is
    the MIGRATED form and is NOT a fork.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    forks: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not _is_re_compile_call(node.value):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if any(token in target.id for token in _REDACTION_DENYLIST_NAME_TOKENS):
                forks.append(target.id)
    return forks


# ---------------------------------------------------------------------------
# Allowlist: files not yet migrated onto the ops/safety kernel in the C-1 pass.
# RATCHET — only shrinks. The first block carries specific per-file
# justifications for the genuinely-tricky sites surfaced during the C-1 pass;
# the remaining entries are batched per-package and will be migrated in
# follow-up C-1 batches (the union kernel already covers their token shapes —
# they are deferred only because each needs a per-wrapper golden-equivalence
# capture before its local copy can be deleted).
# ---------------------------------------------------------------------------
_ALLOWLIST_JUSTIFIED: dict[str, str] = {
    # --- "private-material phrasing" detectors (NOT secret-token regexes;
    #     match human-language descriptions like "hidden reasoning",
    #     "chain of thought", "tool arguments" — a different concern from the
    #     credential denylist). Defer to a phrase-detector consolidation. ---
    "adk_bridge/event_adapter.py": "private-material phrasing detector, not a token denylist",
    "runtime/events.py": "private-material phrasing detector, not a token denylist",
    "runtime/public_events.py": "private-material phrasing detector, not a token denylist",
    "transport/sse.py": "private-material phrasing detector, not a token denylist",
    "shadow/gate3b_metrics.py": "private-material phrasing detector, not a token denylist",
    # --- copies that add domain-specific NON-secret structural tokens
    #     (toolhost/dispatcher/registry/newText/magi_agent.runtime). Migrating
    #     would LOSE those domain tokens; out of scope for C-1's secret dedup. ---
    "harness/coding/code_intelligence_contracts.py": "adds non-secret structural tokens (toolhost/dispatcher/registry/newText)",
    "harness/coding/ownership_projection.py": "adds non-secret structural tokens (toolhost/dispatcher/registry)",
    "recipes/first_party/coding/ownership.py": "adds non-secret structural tokens (toolhost/dispatcher/registry)",
    # --- copies whose .sub()/branch-ordering depends on matching bare
    #     structural words or reason-code ordering that the kernel's broader
    #     compact-fragment match would change (verified by failing parity
    #     tests during the C-1 pass). ---
    "evidence/runtime_receipts.py": "flags bare structural words (auth/session/credentials) via .search; kernel would not scrub them in .sub",
    "evidence/validator_taxonomy.py": "flags bare structural words; bare-word coverage not in kernel scrub path",
    "channels/discord_adapter.py": "_SECRET_TEXT_RE feeds branch-order-sensitive download classification (reason-code parity)",
    "channels/telegram_adapter.py": "_SECRET_TEXT_RE feeds branch-order-sensitive download/url classification (reason-code parity)",
    "runtime/approval_resume.py": "session[_-](key|id) bare match in .sub path not in kernel scrub",
}

# Per-package batch deferral (token shapes covered by the union kernel; each
# needs a golden-equivalence capture of its wrapper before deletion).
_ALLOWLIST_BATCHED: tuple[str, ...] = (
    "artifacts/delivery_boundary.py",
    "artifacts/file_delivery.py",
    "artifacts/output_registry_boundary.py",
    "channels/dispatcher.py",
    "channels/push_delivery.py",
    "channels/runtime_boundary.py",
    "channels/telegram_boundary.py",
    "coding/lsp_client.py",
    "credentials_admin/approvals_store.py",
    "evidence/code_diagnostics_receipts.py",
    "evidence/source_ledger.py",
    "harness/cron_runtime.py",
    "harness/cross_review.py",
    "harness/discipline_boundary.py",
    "harness/general_automation/text_scrub.py",
    "harness/scheduler_runtime.py",
    "knowledge/provider_boundary.py",
    "memory/projection.py",
    "meta_orchestration/task_plan.py",
    "plugins/extension_boundary.py",
    "plugins/shell_testrun_safe_subset.py",
    "recipes/coding_evidence_gate.py",
    "recipes/coding_subagents.py",
    "recipes/first_party/memory_recall.py",
    "recipes/ledger_task.py",
    "recipes/research_child_runner.py",
    "research/acceptance_criteria.py",
    "research/action_claims.py",
    "research/boundary_enforcement.py",
    "research/child_roles.py",
    "research/claim_graph.py",
    "research/evidence_graph.py",
    "research/final_projection_gate.py",
    "research/policy_pack.py",
    "research/repair.py",
    "research/source_proof.py",
    "runtime/child_runner_boundary.py",
    "runtime/no_agent_watchdog.py",
    "runtime/provider_receipts.py",
    # S-03: single receipt secret denylist; receipt_utils.py + missions/receipts.py
    # now import from here (2 forks -> 1). Routes to ops/safety in C-1 follow-up.
    "runtime/receipt_redaction.py",
    "runtime/request_shape.py",
    "runtime/slash_control_boundary.py",
    "runtime/streaming.py",
    "runtime/structured_output_boundary.py",
    "runtime/work_console_snapshot.py",
    "shadow/gate3b_bundle.py",
    "shadow/gate3b_local_report.py",
    "shadow/gate4_consumer.py",
    "tools/schema_projection.py",
    "web_acquisition/policy.py",
    "web_acquisition/reference_research_tools.py",
    "web_acquisition/repo_research_tools.py",
    "workspace/adoption_boundary.py",
)

_ALLOWLIST: dict[str, str] = {
    **_ALLOWLIST_JUSTIFIED,
    **{rel: "C-1 follow-up batch: kernel covers token shapes; golden capture pending" for rel in _ALLOWLIST_BATCHED},
}


def test_no_forked_private_text_re() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _iter_module_files():
        rel = _rel(path)
        if rel in _KERNEL_FILES:
            continue
        forks = _forks_redaction_denylist(path)
        if not forks:
            continue
        if rel in _ALLOWLIST:
            continue
        offenders[rel] = forks

    assert not offenders, (
        "New/un-migrated forked secret-redaction regex found outside ops/safety.py.\n"
        "Migrate the site onto magi_agent.ops.safety (alias _PRIVATE_TEXT_RE to\n"
        "UNSAFE_TEXT_RE, or route the wrapper through redact_private_text /\n"
        "contains_secret_marker), or add it to the documented shrinking allowlist\n"
        "in this test with a justification.\n"
        f"Offenders: {offenders}"
    )


def test_allowlist_entries_still_fork() -> None:
    """Ratchet integrity: every allowlist entry must still actually contain a
    forked redaction denylist. Once a file is migrated, its allowlist entry must
    be deleted (this test fails on a stale entry), so the allowlist only
    shrinks."""
    stale: list[str] = []
    for rel in sorted(_ALLOWLIST):
        path = PACKAGE / rel
        if not path.exists():
            stale.append(f"{rel} (file missing)")
            continue
        if not _forks_redaction_denylist(path):
            stale.append(f"{rel} (no longer forks — delete the allowlist entry)")
    assert not stale, f"Stale allowlist entries (must be removed): {stale}"


def test_migrated_sites_are_clean_and_not_allowlisted() -> None:
    """The C-1 migrated sites must NOT be allowlisted and must no longer fork."""
    migrated = (
        "tools/kernel.py",
        "tools/output_budget.py",
        "tools/schema_validation.py",
        "artifacts/local_result_store.py",
        "runtime/governed_projection.py",
    )
    for rel in migrated:
        assert rel not in _ALLOWLIST, f"{rel} was migrated; remove from allowlist"
        path = PACKAGE / rel
        assert not _forks_redaction_denylist(path), (
            f"{rel} should no longer fork the redaction denylist after migration"
        )


@pytest.mark.parametrize("rel", sorted(_ALLOWLIST))
def test_allowlist_files_exist(rel: str) -> None:
    assert (PACKAGE / rel).exists(), f"allowlisted file {rel} does not exist"
