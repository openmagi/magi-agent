"""Canonical preset id → runtime-seam map for the Customize verification tab.

Single source of truth for which dashboard preset toggles actually drive a
runtime gate, and how. Canonical ids use HYPHENS (matching
``harness/presets.py`` and the hosted product). The catalog, the apply layer,
and the assembly-wiring all import from here so the mapping never drifts.

The recipe-driven pre-final evidence gate is default-ON (full profile) and the
default task profile selects every first-party pack, so the controlled refs are
already required by default. The Customize tab's job for these presets is
therefore **opt-out**: when the user explicitly disables a preset, its controlled
ref is removed from the assembled ``required_validators`` so the gate no longer
blocks on it.

Phase 2 wires exactly one preset whose seam is a clean assembly-layer opt-out:
- ``coding-verification`` — controls the ``verifier:dev-coding:test-evidence``
  required validator (default-ON; disabling removes it).

Other presets are reported honestly (``preview`` = not yet wired; ``always-on`` =
enforced elsewhere, e.g. security presets via the PermissionGate). ``fact-grounding``
is intentionally NOT wired here: its runtime default is OFF (env-flag gated, bare
label inert in the bus) so it needs opt-IN semantics + engine-satisfier plumbing,
handled with the Phase 3 reality-check batch. No fake toggles.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PresetSeam:
    """How an enabled/disabled preset toggle maps to the pre-final gate.

    ``wiring``:
    - ``opt_out`` — the controlled refs are in the default assembly and the gate
      enforces them by default; disabling the preset REMOVES the refs from
      ``required_validators`` (assembly-layer, ``real_runner``). ``controls_refs``
      lists those refs.
    - ``opt_in`` — the enforcement is an env-flag-gated engine satisfier that is
      OFF by default; enabling the preset turns that satisfier on for the runtime
      (engine-layer, via ``customize.runtime_gate.preset_enabled``). The toggle is
      effectively UI for the existing ``MAGI_*`` enforcement flag. ``controls_refs``
      is documentation-only for these.

    ``runtime_default_on`` is the preset's effective default in the LIVE runtime
    (not the catalog's product ``default_on``), used to resolve the unset state.
    """

    preset_id: str
    controls_refs: tuple[str, ...]
    runtime_default_on: bool = True
    supported_modes: tuple[str, ...] = ("deterministic",)
    wiring: str = "opt_out"
    #: Which assembled ref list an ``opt_out`` seam subtracts from: ``"validator"``
    #: (required_validators, the default) or ``"evidence"`` (required_evidence).
    #: Ignored for ``opt_in`` seams (those activate an engine satisfier instead).
    controls_kind: str = "validator"


# Presets with a genuine runtime seam.
#
# Phase 2: coding-verification (opt-out, assembly-layer ref removal).
# Phase 3: fact-grounding / source-authority / artifact-delivery (opt-in; the
# toggle activates the existing env-flag-gated engine satisfier — runtime default
# OFF). The remaining presets are metadata-only / no live producer and stay
# ``preview`` in the catalog. No fake toggles.
PRESET_SEAMS: dict[str, PresetSeam] = {
    "coding-verification": PresetSeam(
        preset_id="coding-verification",
        controls_refs=("verifier:dev-coding:test-evidence",),
        runtime_default_on=True,
        supported_modes=("deterministic",),
        wiring="opt_out",
    ),
    # Opt-out of the recorded git-diff / test-run EVIDENCE the dev-coding pack
    # requires on coding turns (emitted by _inferred_refs). default-ON: disabling
    # the preset removes those evidence refs from the assembled required_evidence
    # (assembly-layer, controls_kind="evidence"), so the gate no longer blocks a
    # coding turn that recorded no git-diff/test-run. Remove-only, like
    # coding-verification; byte-identical with no override.
    "deterministic-evidence": PresetSeam(
        preset_id="deterministic-evidence",
        controls_refs=("evidence:git-diff", "evidence:test-run"),
        runtime_default_on=True,
        supported_modes=("deterministic",),
        wiring="opt_out",
        controls_kind="evidence",
    ),
    "fact-grounding": PresetSeam(
        preset_id="fact-grounding",
        controls_refs=("fact_grounding",),
        runtime_default_on=False,
        supported_modes=("deterministic",),
        wiring="opt_in",
    ),
    "source-authority": PresetSeam(
        preset_id="source-authority",
        controls_refs=("verifier:research-source-evidence",),
        runtime_default_on=False,
        supported_modes=("deterministic",),
        wiring="opt_in",
    ),
    "artifact-delivery": PresetSeam(
        preset_id="artifact-delivery",
        controls_refs=("evidence:artifact-delivery-ref",),
        runtime_default_on=False,
        supported_modes=("deterministic",),
        wiring="opt_in",
    ),
    # Opt-in for the hard-redaction satisfier (credential-clean scan of the final
    # answer + the no-production-attachment invariant). Enabling the preset turns
    # on the engine satisfier (cli/engine _hard_redaction_matched_requirement_labels)
    # for the runtime even when MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED is off.
    # controls_refs is documentation-only for opt_in seams.
    "redaction": PresetSeam(
        preset_id="redaction",
        controls_refs=(
            "public_redaction",
            "no_production_attachment",
            "redaction_audit",
            "no_raw_evidence_payload",
        ),
        runtime_default_on=False,
        supported_modes=("deterministic",),
        wiring="opt_in",
    ),
    # Opt-in for the mandatory evidence-pack satisfier (cli/engine
    # _evidence_pack_matched_requirement_labels): the runtime issued >=1 evidence
    # record this turn + the audit-mode invariant. Enabling the preset turns on
    # the satisfier even when MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED is off.
    # controls_refs is documentation-only for opt_in seams.
    "evidence-pack": PresetSeam(
        preset_id="evidence-pack",
        controls_refs=(
            "runtime_evidence_record",
            "evidence:runtime-issued-record",
            "validator:evidence:no-block-mode",
        ),
        runtime_default_on=False,
        supported_modes=("deterministic",),
        wiring="opt_in",
    ),
    # Opt-in for the 3-mode document-authoring-coverage gate. Enabling the preset
    # promotes the gate to ``block`` for the runtime even when
    # MAGI_DOCUMENT_AUTHORING_COVERAGE is off/default (engine-layer: resolve the
    # mode, then upgrade off→block when the preset is enabled). controls_refs is
    # documentation-only for opt_in seams (the gate is a coverage-mode flip, not
    # a required-validator ref add/remove).
    "document-authoring-coverage": PresetSeam(
        preset_id="document-authoring-coverage",
        controls_refs=("document-authoring-coverage",),
        runtime_default_on=False,
        supported_modes=("deterministic",),
        wiring="opt_in",
    ),
    # Opt-in for the C8 taskboard-completion gate (cli/engine
    # _task_board_completion_block_labels): blocks completion while the workspace
    # .magi/taskboard.jsonl still has a task in a non-terminal status. Enabling
    # the preset turns the gate on even when MAGI_VERIFY_TASKBOARD_COMPLETION is
    # off. controls_refs is documentation-only for opt_in seams.
    "task-board-completion": PresetSeam(
        preset_id="task-board-completion",
        controls_refs=("task_board:incomplete_tasks",),
        runtime_default_on=False,
        supported_modes=("deterministic",),
        wiring="opt_in",
    ),
    # Opt-in for the C6 parallel-research source-count cross-check (cli/engine
    # _parallel_research_missing_labels): a research-recipe turn that synthesized
    # from fewer than the minimum inspected sources is blocked. Enabling the
    # preset turns on the check even when MAGI_VERIFY_PARALLEL_RESEARCH is off.
    # controls_refs is documentation-only for opt_in seams.
    "parallel-research": PresetSeam(
        preset_id="parallel-research",
        controls_refs=("parallel_research:insufficient_sources",),
        runtime_default_on=False,
        supported_modes=("deterministic",),
        wiring="opt_in",
    ),
    # Opt-in for the C9 response-language policy gate (cli/engine
    # _response_language_block_labels): when a language policy is configured
    # (MAGI_RESPONSE_LANGUAGE), a final answer that violates it is blocked by
    # wiring the dormant discipline_boundary.response_language check. Enabling the
    # preset turns the gate on even when MAGI_VERIFY_RESPONSE_LANGUAGE is off.
    # controls_refs is documentation-only for opt_in seams.
    "response-language": PresetSeam(
        preset_id="response-language",
        controls_refs=("response_language:policy_violation",),
        runtime_default_on=False,
        supported_modes=("deterministic",),
        wiring="opt_in",
    ),
    # Opt-in for the C1 answer-quality LLM gate (cli/engine
    # _answer_quality_llm_block): blocks a final answer that does not genuinely
    # address the user's task. LLM tier (criterion judge), so it also requires a
    # critic model (MAGI_EGRESS_GATE_ENABLED). Enabling the preset turns the gate
    # on even when MAGI_VERIFY_ANSWER_QUALITY is off. controls_refs is
    # documentation-only for opt_in seams.
    "answer-quality": PresetSeam(
        preset_id="answer-quality",
        controls_refs=("answer_quality:unaddressed_task",),
        runtime_default_on=False,
        supported_modes=("llm",),
        wiring="opt_in",
    ),
    # Opt-in for the C2 pre-refusal LLM gate (cli/engine _pre_refusal_llm_block):
    # blocks a final answer that prematurely refuses a doable task without any
    # attempt or a legitimate reason. LLM tier, so it also requires a critic model
    # (MAGI_EGRESS_GATE_ENABLED). Enabling the preset turns the gate on even when
    # MAGI_VERIFY_PRE_REFUSAL is off. controls_refs is documentation-only.
    "pre-refusal": PresetSeam(
        preset_id="pre-refusal",
        controls_refs=("pre_refusal:premature_refusal",),
        runtime_default_on=False,
        supported_modes=("llm",),
        wiring="opt_in",
    ),
    # Opt-in for the C-MERGE-1 completion/promise-without-action LLM gate (cli/engine
    # _completion_evidence_llm_block): blocks a final answer that claims completion
    # or promises future delivery while the turn produced no action evidence. ONE
    # producer covers all three concerns — enabling ANY of these presets activates
    # it. LLM tier (needs a critic model, MAGI_EGRESS_GATE_ENABLED). controls_refs
    # is documentation-only for opt_in seams.
    "completion-evidence": PresetSeam(
        preset_id="completion-evidence",
        controls_refs=("completion_evidence:unsupported_claim",),
        runtime_default_on=False,
        supported_modes=("llm",),
        wiring="opt_in",
    ),
    "goal-progress": PresetSeam(
        preset_id="goal-progress",
        controls_refs=("completion_evidence:unsupported_claim",),
        runtime_default_on=False,
        supported_modes=("llm",),
        wiring="opt_in",
    ),
    "deferral-blocker": PresetSeam(
        preset_id="deferral-blocker",
        controls_refs=("completion_evidence:unsupported_claim",),
        runtime_default_on=False,
        supported_modes=("llm",),
        wiring="opt_in",
    ),
    # Opt-in for the C-MERGE-2 resource/self-claim LLM gate (cli/engine
    # _resource_claim_llm_block): blocks a final answer that asserts a specific
    # resource exists / was read / was checked while the turn produced no source
    # /read evidence. ONE producer covers both concerns — enabling EITHER preset
    # activates it. LLM tier (needs a critic model, MAGI_EGRESS_GATE_ENABLED).
    # controls_refs is documentation-only for opt_in seams.
    "self-claim": PresetSeam(
        preset_id="self-claim",
        controls_refs=("resource_claim:unverified_resource",),
        runtime_default_on=False,
        supported_modes=("llm",),
        wiring="opt_in",
    ),
    "resource-existence": PresetSeam(
        preset_id="resource-existence",
        controls_refs=("resource_claim:unverified_resource",),
        runtime_default_on=False,
        supported_modes=("llm",),
        wiring="opt_in",
    ),
}


def seam_for(preset_id: str) -> PresetSeam | None:
    return PRESET_SEAMS.get(preset_id)


def supported_modes_for(preset_id: str) -> tuple[str, ...]:
    seam = PRESET_SEAMS.get(preset_id)
    return seam.supported_modes if seam is not None else ("deterministic",)


# Presets backed by a real runtime CAPABILITY (a behavior the runtime can run),
# gated by an env flag rather than a pre-final verification gate — so the
# Customize tab surfaces them honestly as a capability, not a togglable gate and
# not an unimplemented preview.
#
# coding-child-review is the cross-verify/cross-review multi-model capability
# (recipes/cross_verify.py + harness/cross_review.py, gated by
# MAGI_CROSS_VERIFY_ENABLED). It runs adversarial peer review of sub-agent output;
# it is NOT a pre-final gate satisfier, so it can't be a verification seam (that
# would be a fake toggle). Enable it via the env flag.
_CAPABILITY_PRESETS: dict[str, str] = {
    "coding-child-review": "MAGI_CROSS_VERIFY_ENABLED",
}


def capability_flag_for(preset_id: str) -> str | None:
    """The env flag that enables a capability preset, or None if not a capability."""
    return _CAPABILITY_PRESETS.get(preset_id)


def enforcement_for(preset_id: str, *, category: str, is_security: bool) -> str:
    """Honest enforcement status for the catalog UI.

    ``enforcing``  — toggling this preset changes runtime behavior now.
    ``always-on``  — enforced by the runtime elsewhere (security/PermissionGate),
                     not controllable from this tab.
    ``capability`` — a real runtime capability gated by an env flag (not a
                     pre-final verification gate, so not a Customize toggle).
    ``preview``    — surfaced for parity but not yet wired to a runtime gate.
    """
    if preset_id in PRESET_SEAMS:
        return "enforcing"
    if is_security or category == "security":
        return "always-on"
    if preset_id in _CAPABILITY_PRESETS:
        return "capability"
    return "preview"


# WHEN-group (domain) for the modal, mapped from PresetCategory. The modal groups
# presets by *when they fire* rather than by semantic category (spec §7/D3).
_CATEGORY_TO_DOMAIN: dict[str, str] = {
    "security": "always-on",
    "coding": "coding",
    "research": "research",
    "fact": "research",
    "answer": "delivery",
    "output": "delivery",
    "task": "delivery",
    "memory": "delivery",
}


def domain_for(category: str) -> str:
    """Map a PresetCategory to a modal WHEN-group (always-on/coding/research/delivery)."""
    return _CATEGORY_TO_DOMAIN.get(category, "delivery")


def tier_for(preset_id: str, *, is_security: bool) -> str | None:
    """Enforcement mechanism tier for the badge.

    ``deterministic`` — a wired pre-final ref check.
    ``llm``           — a wired pre-final LLM criterion judge (needs a critic model).
    ``always-on``     — security/PermissionGate, immutable.
    ``None``          — preview (no runtime gate yet).

    For a wired seam the tier is its first ``supported_modes`` entry, so an
    LLM-tier seam badges ``llm`` rather than falsely claiming ``deterministic``.
    """
    seam = PRESET_SEAMS.get(preset_id)
    if seam is not None:
        return seam.supported_modes[0] if seam.supported_modes else "deterministic"
    if is_security:
        return "always-on"
    return None


def opt_method_for(preset_id: str) -> str | None:
    """``opt-out`` / ``opt-in`` from the seam wiring, or None if not wired."""
    seam = PRESET_SEAMS.get(preset_id)
    if seam is None:
        return None
    return "opt-out" if seam.wiring == "opt_out" else "opt-in"


# Concrete one-line descriptions. The 4 WIRED presets use OSS-accurate wording
# (spec §4.3/§5 — e.g. source-authority is anti-fabrication, NOT the hosted
# "memory vs real-time" copy). Security presets describe the always-on guardrail.
# Remaining (preview) presets reuse the hosted intent copy so the UI explains what
# each WOULD check — they stay honestly badged as preview elsewhere.
_DESCRIPTIONS: dict[str, str] = {
    # --- wired (OSS-accurate) ---
    "coding-verification": "Require fresh test-pass evidence before the final answer when code is mutated.",
    "fact-grounding": "Block a specific factual value in the answer that isn't grounded in opened sources.",
    "source-authority": "Require declared citations to point at actually-inspected sources (anti-fab).",
    "artifact-delivery": "Require real delivery evidence for promised artifacts before completion.",
    "redaction": "Block a final answer that leaks a credential and require the no-production-attachment invariant.",
    "evidence-pack": "Require the runtime to have issued at least one evidence record this turn (audit-mode).",
    # --- always-on security ---
    "dangerous-patterns": "Block dangerous shell commands. Always-on safety.",
    "path-escape": "Block file access outside the workspace. Always-on safety.",
    "secret-exposure": "Block commands that would expose secrets or credentials. Always-on safety.",
    "git-safety": "Block destructive git operations. Always-on safety.",
    "sealed-files": "Protect sealed files from modification. Always-on safety.",
    "arity-permission": "Require permission for high-impact tool actions. Always-on safety.",
    # --- preview (hosted intent copy; honestly badged preview in the catalog) ---
    "answer-quality": "Block a final answer that doesn't genuinely address the task (LLM judge; needs a critic model).",
    "completion-evidence": "Block a completion/promise claim made with no action evidence this turn (LLM judge; needs a critic model).",
    "pre-refusal": "Block a premature refusal of a doable task (LLM judge; needs a critic model).",
    "output-purity": "Blocks raw JSON or internal data from appearing in responses.",
    "deferral-blocker": "Block a promise of future delivery made with no action evidence this turn (LLM judge; shares the completion-evidence gate).",
    "self-claim": "Block a claim about file/URL/memory contents made with no read evidence this turn (LLM judge; needs a critic model).",
    "resource-existence": "Block an assertion that a specific file/URL exists when the turn inspected no source (LLM judge; shares the self-claim gate).",
    "claim-citation": "Ensures factual claims include sources.",
    "deterministic-evidence": "Require recorded git-diff and test-run evidence on coding turns (disable to opt out).",
    "coding-context": "Auto-injects repo map and symbols for code tasks.",
    "coding-workspace-lock": "Prevents unrelated file changes during coding.",
    "coding-child-review": "Adversarial multi-model review of sub-agent output. Capability — enable with MAGI_CROSS_VERIFY_ENABLED.",
    "benchmark-verifier": "Detects and blocks performance regressions.",
    "task-contract": "Enforces a goal to plan to evidence lifecycle.",
    "goal-progress": "Block a completion claim made with no action evidence this turn (LLM judge; shares the completion-evidence gate).",
    "task-board-completion": "Blocks completion when tasks remain incomplete.",
    "parallel-research": "Block a research turn that synthesized from fewer than 2 inspected sources.",
    "output-delivery": "Verifies created files are actually delivered.",
    "response-language": "Block a final answer that violates the configured language policy (MAGI_RESPONSE_LANGUAGE).",
    "parallel-research": "Verifies and cross-checks research sources.",
    "memory-continuity": "Maintains cross-session memory consistency.",
    "document-authoring-coverage": "Checks authored documents cover the requested scope.",
}

_DESCRIPTION_FALLBACK = "Surfaced for parity; not yet wired to a runtime gate."


def description_for(preset_id: str) -> str:
    """Concrete one-line description for a preset (honest fallback if unknown)."""
    return _DESCRIPTIONS.get(preset_id, _DESCRIPTION_FALLBACK)
