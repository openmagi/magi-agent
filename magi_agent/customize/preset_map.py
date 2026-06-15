"""Canonical preset id → runtime-seam map for the Customize verification tab.

Single source of truth for which dashboard preset toggles actually drive a
runtime gate, and how. Canonical ids use HYPHENS (matching
``harness/presets.py`` and the hosted product). The catalog, the apply layer,
and the assembly-wiring all import from here so the mapping never drifts.

Phase 2 wires only the presets whose runtime seam genuinely exists today:
- ``coding-verification`` — forces the ``verifier:dev-coding:test-evidence``
  required validator (and keeps the ``openmagi.dev-coding`` pack selected so the
  pre-final gate stays mutation-scoped).
- ``fact-grounding`` — injects the bare ``fact_grounding`` evidence label so the
  deterministic grounding satisfier runs. Deterministic mode only — there is no
  LLM-grounding code path yet.

Everything else is reported honestly (``preview`` = not yet wired; ``always-on``
= enforced elsewhere, e.g. security presets via the PermissionGate) until later
phases wire it. No fake toggles.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PresetSeam:
    """How an enabled preset contributes to the recipe-driven pre-final gate."""

    preset_id: str
    supported_modes: tuple[str, ...] = ("deterministic",)
    enforcement: str = "enforcing"
    # Public validator refs (``verifier:``/``evidence:`` prefixed) appended to the
    # assembly's required_validators when the preset is enabled.
    validator_refs: tuple[str, ...] = ()
    # Bare evidence-requirement labels (engine-satisfier driven, e.g.
    # ``fact_grounding``) appended to required_validators when enabled.
    evidence_labels: tuple[str, ...] = ()
    # Pack ids that must be present in selectedPackIds when the preset is enabled
    # (preserves mutation-scoping for coding gates).
    require_packs: tuple[str, ...] = ()


# Presets with a genuine runtime seam wired in Phase 2.
PRESET_SEAMS: dict[str, PresetSeam] = {
    "coding-verification": PresetSeam(
        preset_id="coding-verification",
        supported_modes=("deterministic",),
        validator_refs=("verifier:dev-coding:test-evidence",),
        require_packs=("openmagi.dev-coding",),
    ),
    "fact-grounding": PresetSeam(
        preset_id="fact-grounding",
        supported_modes=("deterministic",),
        evidence_labels=("fact_grounding",),
    ),
}


def seam_for(preset_id: str) -> PresetSeam | None:
    return PRESET_SEAMS.get(preset_id)


def supported_modes_for(preset_id: str) -> tuple[str, ...]:
    seam = PRESET_SEAMS.get(preset_id)
    return seam.supported_modes if seam is not None else ("deterministic",)


def enforcement_for(preset_id: str, *, category: str, is_security: bool) -> str:
    """Honest enforcement status for the catalog UI.

    ``enforcing``  — toggling this preset changes runtime behavior now.
    ``always-on``  — enforced by the runtime elsewhere (security/PermissionGate),
                     not controllable from this tab.
    ``preview``    — surfaced for parity but not yet wired to a runtime gate.
    """
    if preset_id in PRESET_SEAMS:
        return "enforcing"
    if is_security or category == "security":
        return "always-on"
    return "preview"
