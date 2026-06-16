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

    ``controls_refs`` are the public validator refs this preset governs. When the
    preset resolves enabled they are ensured present in ``required_validators``;
    when explicitly disabled they are removed. ``runtime_default_on`` is the
    preset's effective default in the live runtime (NOT the catalog's product
    ``default_on``, which can differ), used to resolve the unset state.
    """

    preset_id: str
    controls_refs: tuple[str, ...]
    runtime_default_on: bool = True
    supported_modes: tuple[str, ...] = ("deterministic",)


# Presets with a genuine assembly-layer seam wired in Phase 2.
PRESET_SEAMS: dict[str, PresetSeam] = {
    "coding-verification": PresetSeam(
        preset_id="coding-verification",
        controls_refs=("verifier:dev-coding:test-evidence",),
        runtime_default_on=True,
        supported_modes=("deterministic",),
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
