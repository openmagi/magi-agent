"""Track 19 PR6 — per-turn GA constraint re-injection (compaction-proof).

The constraint-reinjection hook re-emits, every turn, the active GA contract's
still-unmet required-evidence checklist plus any open ``approval_required``
controls — so the model never "forgets" what it still owes over a long task
(mirrors OpenCode re-emitting the plan reminder each turn).

It is a small pure function (:func:`build_ga_constraint_reminder`) plus a
flag-gated builder (:func:`ga_constraint_reinjection`) that gates on
``MAGI_GA_LIVE_ENABLED`` + ``agent_role == "general"`` and is otherwise inert
(no contribution / no injection), byte-identical to ``main``.

The reminder text is digest/label-only — it never carries raw paths, commands,
or secrets, consistent with the codebase's scrubbing discipline.
"""
from __future__ import annotations

from magi_agent.evidence.ledger import EvidenceLedger
from magi_agent.harness.general_automation.constraint_reinjection import (
    GA_CONSTRAINT_REINJECTION_HOOK_NAME,
    build_ga_constraint_reminder,
    ga_constraint_reinjection,
    ga_constraint_reinjection_hook_manifest,
)
from magi_agent.harness.general_automation.control_projection import (
    GeneralAutomationControlProjection,
    GeneralAutomationControlProjectionRequest,
    build_general_automation_control_projection,
)
from magi_agent.harness.general_automation.task_completion import (
    RequiredDeliverableEvidence,
)
from magi_agent.hooks.manifest import HookPoint
from magi_agent.hooks.scope import HookScope, HookScopeContext


_SECRET = "super-secret-token-value"
_RAW_PATH = "/etc/passwd"
_RAW_COMMAND = "rm -rf /home/ocuser/.openclaw"


def _ledger() -> EvidenceLedger:
    return EvidenceLedger(
        ledgerId="ledger-session-1-turn-1",
        sessionId="session-1",
        turnId="turn-1",
        runOn="main",
        agentRole="general",
        spawnDepth=0,
        sourceKind="tool_trace",
        producerSurface="tool_host",
    )


def _required_artifact() -> RequiredDeliverableEvidence:
    return RequiredDeliverableEvidence(requires_artifact_ref=True)


def _approval_control() -> GeneralAutomationControlProjection:
    # subject/payload digests are sha256 over the (raw) path/command — never the
    # raw value itself, mirroring the live gate's control projections.
    digest = "sha256:" + "a" * 64
    request = GeneralAutomationControlProjectionRequest(
        controlType="approval_required",
        subjectRef=digest,
        policyRef="policy:general-automation:path-policy",
        payloadDigest=digest,
        reasonCodes=("external_directory_requires_approval",),
        approvalRef="approval:external-directory:" + digest,
    )
    return build_general_automation_control_projection(request)


def _resolved_control() -> GeneralAutomationControlProjection:
    digest = "sha256:" + "b" * 64
    request = GeneralAutomationControlProjectionRequest(
        controlType="resume_ready",
        subjectRef=digest,
        policyRef="policy:general-automation:path-policy",
        payloadDigest=digest,
    )
    return build_general_automation_control_projection(request)


# ---------------------------------------------------------------------------
# (a) owed evidence present in contract but missing from ledger
# ---------------------------------------------------------------------------


def test_reminder_lists_unmet_required_evidence() -> None:
    reminder = build_ga_constraint_reminder(
        contract_required=_required_artifact(),
        ledger=_ledger(),
        open_controls=(),
    )
    assert reminder is not None
    assert "artifactRef" in reminder


# ---------------------------------------------------------------------------
# (b) all satisfied → returns None
# ---------------------------------------------------------------------------


def test_reminder_none_when_all_satisfied() -> None:
    ledger = _ledger().append_artifact_ref("artifact:spreadsheet:out")
    reminder = build_ga_constraint_reminder(
        contract_required=_required_artifact(),
        ledger=ledger,
        open_controls=(),
    )
    assert reminder is None


def test_reminder_none_when_nothing_required_and_no_controls() -> None:
    reminder = build_ga_constraint_reminder(
        contract_required=RequiredDeliverableEvidence(),
        ledger=_ledger(),
        open_controls=(),
    )
    assert reminder is None


# ---------------------------------------------------------------------------
# (c) open approval_required control → included
# ---------------------------------------------------------------------------


def test_reminder_includes_open_approval_control() -> None:
    reminder = build_ga_constraint_reminder(
        contract_required=RequiredDeliverableEvidence(),
        ledger=_ledger(),
        open_controls=(_approval_control(),),
    )
    assert reminder is not None
    assert _approval_control().control_ref in reminder


def test_reminder_ignores_non_approval_controls() -> None:
    reminder = build_ga_constraint_reminder(
        contract_required=RequiredDeliverableEvidence(),
        ledger=_ledger(),
        open_controls=(_resolved_control(),),
    )
    # resume_ready is not an open obligation → nothing owed
    assert reminder is None


def test_reminder_combines_evidence_and_controls() -> None:
    reminder = build_ga_constraint_reminder(
        contract_required=_required_artifact(),
        ledger=_ledger(),
        open_controls=(_approval_control(), _resolved_control()),
    )
    assert reminder is not None
    assert "artifactRef" in reminder
    assert _approval_control().control_ref in reminder
    assert _resolved_control().control_ref not in reminder


# ---------------------------------------------------------------------------
# (d) flag-OFF / non-general → inert / no contribution
# ---------------------------------------------------------------------------


def test_inert_when_flag_off() -> None:
    decision = ga_constraint_reinjection(
        contract_required=_required_artifact(),
        ledger=_ledger(),
        open_controls=(_approval_control(),),
        agent_role="general",
        env={"MAGI_GA_LIVE_ENABLED": "0"},
    )
    assert decision is None


def test_inert_when_flag_absent() -> None:
    decision = ga_constraint_reinjection(
        contract_required=_required_artifact(),
        ledger=_ledger(),
        open_controls=(_approval_control(),),
        agent_role="general",
        env={"MAGI_GA_LIVE_ENABLED": "0"},
    )
    assert decision is None


def test_inert_when_non_general_role() -> None:
    decision = ga_constraint_reinjection(
        contract_required=_required_artifact(),
        ledger=_ledger(),
        open_controls=(_approval_control(),),
        agent_role="coding",
        env={"MAGI_GA_LIVE_ENABLED": "1"},
    )
    assert decision is None


def test_active_when_flag_on_and_general() -> None:
    decision = ga_constraint_reinjection(
        contract_required=_required_artifact(),
        ledger=_ledger(),
        open_controls=(_approval_control(),),
        agent_role="general",
        env={"MAGI_GA_LIVE_ENABLED": "1"},
    )
    assert decision is not None
    assert "artifactRef" in decision


def test_active_but_returns_none_when_nothing_owed() -> None:
    ledger = _ledger().append_artifact_ref("artifact:spreadsheet:out")
    decision = ga_constraint_reinjection(
        contract_required=_required_artifact(),
        ledger=ledger,
        open_controls=(),
        agent_role="general",
        env={"MAGI_GA_LIVE_ENABLED": "1"},
    )
    assert decision is None


# ---------------------------------------------------------------------------
# (e) reminder contains no raw path / command / secret
# ---------------------------------------------------------------------------


def test_reminder_has_no_raw_path_command_or_secret() -> None:
    reminder = build_ga_constraint_reminder(
        contract_required=_required_artifact(),
        ledger=_ledger(),
        open_controls=(_approval_control(),),
    )
    assert reminder is not None
    assert _SECRET not in reminder
    assert _RAW_PATH not in reminder
    assert _RAW_COMMAND not in reminder
    # no absolute filesystem paths leak into the reminder
    assert "/etc/" not in reminder
    assert "/home/" not in reminder


# ---------------------------------------------------------------------------
# Hook manifest: general-scoped, non-blocking, fail-open, BEFORE_LLM_CALL
# ---------------------------------------------------------------------------


def test_hook_manifest_is_general_scoped_before_llm_call() -> None:
    manifest = ga_constraint_reinjection_hook_manifest()
    assert manifest.name == GA_CONSTRAINT_REINJECTION_HOOK_NAME
    assert manifest.point == HookPoint.BEFORE_LLM_CALL
    assert manifest.scope.scope == "general"
    assert manifest.security_critical is False
    # general-scoped: applies to general main runs, not coding/research.
    assert manifest.scope.applies_to(
        HookScopeContext(runOn="main", agentRole="general", spawnDepth=0)
    )
    assert not manifest.scope.applies_to(
        HookScopeContext(runOn="main", agentRole="coding", spawnDepth=0)
    )


def test_hook_manifest_listed_in_resolved_general_pack() -> None:
    from magi_agent.harness.resolved import build_default_resolved_harness_state

    state = build_default_resolved_harness_state(agent_role="general")
    hooks = state.general.components["hooks"]
    assert GA_CONSTRAINT_REINJECTION_HOOK_NAME in hooks


def test_recipe_pack_declares_reinjection_callback_ref() -> None:
    from magi_agent.harness.general_automation.constraint_reinjection import (
        GA_CONSTRAINT_REINJECTION_CALLBACK_REF,
    )
    from magi_agent.recipes.compiler import PackRegistry

    catalog = PackRegistry.with_first_party_packs().values()
    ga_pack_ids = {
        "openmagi.office-automation",
        "openmagi.spreadsheet-automation",
        "openmagi.browser-automation",
        "openmagi.artifact-delivery",
    }
    ga_packs = [pack for pack in catalog if pack.pack_id in ga_pack_ids]
    assert ga_packs, "expected the first-party GA recipe packs in the catalog"
    for pack in ga_packs:
        assert GA_CONSTRAINT_REINJECTION_CALLBACK_REF in pack.callback_refs


def test_compiler_callback_ref_literal_matches_canonical_constant() -> None:
    # compiler.py keeps a plain-literal copy (to stay off the heavy import
    # chain); it MUST equal the canonical constant.
    from magi_agent.harness.general_automation.constraint_reinjection import (
        GA_CONSTRAINT_REINJECTION_CALLBACK_REF as canonical,
    )
    from magi_agent.recipes.compiler import (
        GA_CONSTRAINT_REINJECTION_CALLBACK_REF as compiler_literal,
    )

    assert compiler_literal == canonical


# ---------------------------------------------------------------------------
# Hook projection extension: metadata-only, callbackAttached stays False
# ---------------------------------------------------------------------------


def test_constraint_reinjection_projection_is_metadata_only() -> None:
    from magi_agent.plugins.general_automation.hook_projection import (
        project_constraint_reinjection_callback,
    )

    projection = project_constraint_reinjection_callback()
    assert projection.status == "projected_metadata"
    assert projection.callback_names == ("before_model_callback",)
    # No authority flag is flipped — declaration only.
    assert projection.authority_flags.callback_attached is False
    public = projection.public_projection()
    assert public["adkBoundary"]["callbackAttached"] is False


# ---------------------------------------------------------------------------
# Role normalisation: general-automation → general_automation → NOT "general"
# ---------------------------------------------------------------------------


def test_role_general_automation_is_not_treated_as_general() -> None:
    # "general-automation" normalises to "general_automation" (hyphens →
    # underscores), which does NOT equal "general", so the hook is inert.
    decision = ga_constraint_reinjection(
        contract_required=_required_artifact(),
        ledger=_ledger(),
        open_controls=(_approval_control(),),
        agent_role="general-automation",
        env={"MAGI_GA_LIVE_ENABLED": "1"},
    )
    assert decision is None


# ---------------------------------------------------------------------------
# Import boundary: constraint_reinjection must not load magi_agent.transport
# at module import time (only when ga_constraint_reinjection_hook_manifest()
# is called).
# ---------------------------------------------------------------------------


def test_constraint_reinjection_has_no_transport_import_at_module_load() -> None:
    import subprocess
    import sys

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module(
    "magi_agent.harness.general_automation.constraint_reinjection"
)
if "magi_agent.transport" in sys.modules:
    raise AssertionError(
        "magi_agent.transport was imported at module load — "
        "hook-manifest imports must stay deferred inside "
        "ga_constraint_reinjection_hook_manifest()"
    )
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
