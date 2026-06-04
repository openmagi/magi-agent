"""Track 19 PR6 — per-turn General-Automation constraint re-injection.

This module ports OpenCode's per-turn plan-reminder re-injection to the
``general`` agent role: each turn, re-inject into context a compact reminder of
the active GA contract's **still-unmet required-evidence checklist** plus any
**open ``approval_required`` controls** — so the model never "forgets" what it
still owes over a long task (compaction-proof, because the reminder is rebuilt
from the immutable ledger / live controls every turn rather than relying on
earlier transcript text surviving compaction).

It extends the EXISTING General-Automation pack structures (it does NOT add a
new pack):

* :func:`build_ga_constraint_reminder` reuses PR3's
  :class:`~magi_agent.harness.general_automation.task_completion.TaskCompletionVerifier`
  (the same immutable-ledger read that derives still-owed required evidence) and
  PR2's :class:`~magi_agent.harness.general_automation.control_projection.GeneralAutomationControlProjection`
  (the open ``approval_required`` controls). No logic is duplicated.
* :func:`ga_constraint_reinjection_hook_manifest` declares a ``general``-scoped
  :class:`~magi_agent.hooks.manifest.HookManifest` referenced by the resolved
  ``general`` pack's ``hooks`` tuple (``harness/resolved.py``) and by the
  first-party GA recipe packs' ``callbackRefs`` (``recipes/compiler.py``).

Activation requires BOTH (mirroring PR2/PR3):

* ``MAGI_GA_LIVE_ENABLED`` truthy (single-source flag, default OFF), and
* ``agent_role == "general"``.

When inactive — non-general role or flag-OFF — :func:`ga_constraint_reinjection`
returns ``None`` (no injection / no contribution), so behavior is
byte-identical to ``main``. The reminder text is digest/label-only: it carries
required-evidence *labels* and control *refs* (sha256-based), never raw paths,
commands, or secrets.

Wiring seam: the runner must consult :func:`ga_constraint_reinjection` at the
``BEFORE_LLM_CALL`` hook point each turn and prepend the returned reminder to
the model-visible context. As with PR3's ``completion_repair_decision`` and
PR5's max-steps brake, this is an intentionally-unwired-but-tested seam: the
production hook bus is not consulted per-turn for ``general`` runs yet, so the
manifest + builder are declared and exercised by tests, ready for the runner to
attach without inventing a new pack.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from magi_agent.config.env import general_automation_live_enabled
from magi_agent.hooks.manifest import HookManifest, HookPoint
from magi_agent.hooks.scope import HookScope
from magi_agent.tools.manifest import ToolSource

if TYPE_CHECKING:
    from magi_agent.evidence.ledger import EvidenceLedger
    from magi_agent.harness.general_automation.control_projection import (
        GeneralAutomationControlProjection,
    )
    from magi_agent.harness.general_automation.task_completion import (
        RequiredDeliverableEvidence,
    )


#: Name of the general-scoped constraint-reinjection hook. Referenced by the
#: resolved ``general`` pack ``hooks`` tuple in ``harness/resolved.py``.
GA_CONSTRAINT_REINJECTION_HOOK_NAME = "general-automation-constraint-reinjection"

#: Callback ref the first-party GA recipe packs declare in ``callbackRefs``
#: (``recipes/compiler.py``) to surface this re-injection callback as pack
#: metadata.
GA_CONSTRAINT_REINJECTION_CALLBACK_REF = (
    "callback:general-automation:constraint-reinjection"
)

_REINJECTION_SOURCE = ToolSource(
    kind="builtin",
    package="magi_agent.harness.general_automation",
)

#: Only this control type represents an *open obligation* the model still owes.
#: Other control types (resume_ready / artifact_recorded / …) are not pending.
_OPEN_OBLIGATION_CONTROL_TYPE = "approval_required"


def build_ga_constraint_reminder(
    *,
    contract_required: RequiredDeliverableEvidence,
    ledger: EvidenceLedger,
    open_controls: Sequence[GeneralAutomationControlProjection] = (),
) -> str | None:
    """Build the per-turn constraint reminder, or ``None`` when nothing is owed.

    Pure function (no flag / role check; that lives in
    :func:`ga_constraint_reinjection`). It composes two read-only signals:

    * **still-unmet required evidence** — derived by reusing PR3's
      :class:`TaskCompletionVerifier` over the immutable *ledger*; the verdict's
      ``missing`` labels are the still-owed deliverable refs.
    * **open approval controls** — every ``open_controls`` entry whose
      ``control_type`` is ``approval_required`` (an unresolved obligation),
      named by its public ``control_ref``.

    Returns ``None`` when both signals are empty. The text is digest/label-only
    and never includes raw paths, commands, or secrets.
    """
    missing = _missing_required_evidence(contract_required, ledger)
    open_approvals = _open_approval_refs(open_controls)
    if not missing and not open_approvals:
        return None
    return _render_reminder(missing, open_approvals)


def ga_constraint_reinjection(
    *,
    contract_required: RequiredDeliverableEvidence,
    ledger: EvidenceLedger,
    open_controls: Sequence[GeneralAutomationControlProjection] = (),
    agent_role: str,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Flag-gated per-turn reminder, or ``None`` when inert.

    Returns ``None`` (no injection / no contribution) when ``MAGI_GA_LIVE_ENABLED``
    is OFF or ``agent_role`` is not ``general`` — keeping flag-OFF / non-general
    behavior byte-identical to ``main``. Otherwise delegates to
    :func:`build_ga_constraint_reminder` (which itself returns ``None`` when
    nothing is owed).
    """
    if not general_automation_live_enabled(env):
        return None
    if _normalize_role(agent_role) != "general":
        return None
    return build_ga_constraint_reminder(
        contract_required=contract_required,
        ledger=ledger,
        open_controls=open_controls,
    )


def ga_constraint_reinjection_hook_manifest() -> HookManifest:
    """Manifest for the general-scoped constraint-reinjection hook.

    ``general``-scoped, non-security-critical, fail-open, non-blocking, and
    disabled-by-default at the manifest level — the live flag gate
    (:func:`general_automation_live_enabled`) is the authority for activation.
    Runs at ``BEFORE_LLM_CALL`` so the reminder is rebuilt and re-injected every
    turn (compaction-proof).
    """
    return HookManifest(
        name=GA_CONSTRAINT_REINJECTION_HOOK_NAME,
        point=HookPoint.BEFORE_LLM_CALL,
        description=(
            "Re-injects the active GA contract's still-unmet required-evidence "
            "checklist and open approval_required controls each turn "
            "(compaction-proof). Digest/label-only; flag-gated, inert by default."
        ),
        source=_REINJECTION_SOURCE,
        executionType="handler",
        enabled=False,
        failOpen=True,
        blocking=False,
        priority=60,
        optOut=True,
        scope=HookScope(scope="general", agentRoles=("general",)),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _missing_required_evidence(
    contract_required: RequiredDeliverableEvidence,
    ledger: EvidenceLedger,
) -> tuple[str, ...]:
    if contract_required.is_empty():
        return ()
    # Deferred import: keeps this module (and the recipe-pack catalog that
    # imports the callback-ref constant) free of the task_completion →
    # runtime.turn_policy → transport import chain, preserving the recipe
    # materializer's import boundary.
    from magi_agent.harness.general_automation.task_completion import (
        TaskCompletionVerifier,
    )

    verdict = TaskCompletionVerifier().evaluate(ledger, contract_required)
    if verdict.status == "pass":
        return ()
    return verdict.missing


def _open_approval_refs(
    open_controls: Sequence[GeneralAutomationControlProjection],
) -> tuple[str, ...]:
    refs: list[str] = []
    for control in open_controls:
        if control.control_type != _OPEN_OBLIGATION_CONTROL_TYPE:
            continue
        if control.control_ref not in refs:
            refs.append(control.control_ref)
    return tuple(refs)


def _render_reminder(
    missing: tuple[str, ...],
    open_approvals: tuple[str, ...],
) -> str:
    lines = [
        "Outstanding obligations for this task (re-injected each turn — do not "
        "finalise until cleared):",
    ]
    if missing:
        lines.append("Required deliverable evidence still owed:")
        lines.extend(f"- {label}" for label in missing)
    if open_approvals:
        lines.append("Open approval_required controls awaiting resolution:")
        lines.extend(f"- {ref}" for ref in open_approvals)
    return "\n".join(lines)


def _normalize_role(agent_role: str) -> str:
    return agent_role.strip().casefold().replace("-", "_")


__all__ = [
    "GA_CONSTRAINT_REINJECTION_CALLBACK_REF",
    "GA_CONSTRAINT_REINJECTION_HOOK_NAME",
    "build_ga_constraint_reminder",
    "ga_constraint_reinjection",
    "ga_constraint_reinjection_hook_manifest",
]
