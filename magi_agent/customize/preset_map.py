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
