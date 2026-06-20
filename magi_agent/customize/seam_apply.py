"""Apply a :class:`SeamSpec` IR to the static :data:`PRESET_SEAMS` catalog.

Stage B of the PresetSeam NL-spec series. PR-C1 introduced the spec model +
deterministic validator; this module is the **pure** function that translates
an approved spec into a merged ``preset_id → PresetSeam`` mapping the runtime
can consult.

Pure / no I/O / no LLM
======================

``apply_spec_to_seams`` MUST stay pure: it returns a *new* dict; it never
mutates the input ``base_seams``, never reads from disk, and never calls a
model. Persistence (loading the saved spec from the user's customize.json)
and runtime lookup (``seam_for_user``) sit one layer above this function so
they can be reasoned about independently.

Validation gate
===============

Callers SHOULD invoke :func:`magi_agent.customize.seam_spec.validate_spec`
first and refuse to apply when it returns issues. To make accidental
"apply broken spec" a deterministic failure rather than a silent partial
mutation, :func:`apply_spec_to_seams` runs the same validator and raises
:class:`ValueError` on any issue — there is no half-applied state to clean
up. If a spec passes validation against the *current* builtin catalog and
the catalog later drops a preset, the next apply re-validates and raises;
the persisted spec is preserved for the user to fix.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from magi_agent.customize.preset_map import PresetSeam
from magi_agent.customize.seam_spec import SeamSpec, validate_spec


def apply_spec_to_seams(
    spec: SeamSpec,
    base_seams: Mapping[str, PresetSeam],
) -> dict[str, PresetSeam]:
    """Return a new map = ``base_seams`` plus the mutations described in ``spec``.

    ``add_seam``:
        Inserts a brand-new ``PresetSeam`` built from the action fields.
        Validation guarantees the ``preset_id`` does NOT already exist in
        ``base_seams`` so no silent overwrite is possible.

    ``modify_seam``:
        Field-level override on an existing ``PresetSeam``. Only fields with
        non-None action values are replaced; the remaining fields are
        inherited verbatim from the base. Validation guarantees the
        ``preset_id`` already exists in ``base_seams``.

    Parameters
    ----------
    spec:
        The approved :class:`SeamSpec`. Re-validated here so a malformed
        spec is a deterministic ``ValueError`` rather than a partial mutation.
    base_seams:
        The map to layer on top of — almost always
        :data:`magi_agent.customize.preset_map.PRESET_SEAMS`. Passing it in
        keeps this function pure (no module-state read) and makes the unit
        tests easy: hand a tiny test base in.

    Returns
    -------
    dict[str, PresetSeam]
        A *new* dict combining base + mutations. The input mapping is never
        mutated; identity-equal seams from the base survive unchanged so
        downstream code that compares with ``is`` still works for untouched
        presets.

    Raises
    ------
    ValueError
        If the spec contains any structural issue (unknown op, modify
        targeting a non-builtin, add colliding with a builtin, etc.). The
        message lists every issue at once — no partial apply has happened.
    """
    issues = validate_spec(spec)
    if issues:
        joined = "; ".join(issues)
        raise ValueError(f"spec has structural issues, refusing to apply: {joined}")

    merged: dict[str, PresetSeam] = dict(base_seams)

    for action in spec.actions:
        if action.op == "add_seam":
            # validate_spec ensures every required field is set.
            assert action.controls_refs is not None  # noqa: S101 — validated
            assert action.runtime_default_on is not None  # noqa: S101
            assert action.wiring is not None  # noqa: S101
            assert action.controls_kind is not None  # noqa: S101
            supported = action.supported_modes or ("deterministic",)
            merged[action.preset_id] = PresetSeam(
                preset_id=action.preset_id,
                controls_refs=action.controls_refs,
                runtime_default_on=action.runtime_default_on,
                supported_modes=supported,
                wiring=action.wiring,
                controls_kind=action.controls_kind,
            )
        elif action.op == "modify_seam":
            base = merged[action.preset_id]
            updates: dict = {}
            if action.controls_refs is not None:
                updates["controls_refs"] = action.controls_refs
            if action.runtime_default_on is not None:
                updates["runtime_default_on"] = action.runtime_default_on
            if action.wiring is not None:
                updates["wiring"] = action.wiring
            if action.controls_kind is not None:
                updates["controls_kind"] = action.controls_kind
            if action.supported_modes is not None:
                updates["supported_modes"] = action.supported_modes
            if updates:
                merged[action.preset_id] = replace(base, **updates)

    return merged


__all__ = ["apply_spec_to_seams"]
