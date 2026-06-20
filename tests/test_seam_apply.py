"""PR-C2 — apply_spec_to_seams: pure SeamSpec → merged PresetSeam map.

Validation re-runs inside ``apply_spec_to_seams`` so any structural issue is
a deterministic ``ValueError`` rather than a partial mutation. The input
base mapping is NEVER mutated; identity-equal seams from the base survive
unchanged for downstream ``is``-comparisons.
"""

from __future__ import annotations

import pytest

from magi_agent.customize.preset_map import PRESET_SEAMS, PresetSeam
from magi_agent.customize.seam_apply import apply_spec_to_seams
from magi_agent.customize.seam_spec import SeamAction, SeamSpec


def _builtin_id() -> str:
    return "coding-verification"  # stable in PRESET_SEAMS


def _base() -> dict[str, PresetSeam]:
    return dict(PRESET_SEAMS)


def test_empty_spec_returns_copy_of_base() -> None:
    spec = SeamSpec(spec_version="0.1", actions=())
    out = apply_spec_to_seams(spec, _base())
    assert out == dict(PRESET_SEAMS)


def test_input_base_is_not_mutated() -> None:
    base = _base()
    snapshot = dict(base)
    spec = SeamSpec(
        spec_version="0.1",
        actions=(
            SeamAction(
                op="modify_seam", preset_id=_builtin_id(), wiring="opt_in"
            ),
        ),
    )
    apply_spec_to_seams(spec, base)
    assert base == snapshot


def test_untouched_seams_survive_by_identity() -> None:
    # A seam not touched by the spec must be the SAME object in the output —
    # downstream code that compares with `is` should still match.
    base = _base()
    spec = SeamSpec(
        spec_version="0.1",
        actions=(
            SeamAction(
                op="modify_seam", preset_id=_builtin_id(), wiring="opt_in"
            ),
        ),
    )
    out = apply_spec_to_seams(spec, base)
    for preset_id, original in base.items():
        if preset_id == _builtin_id():
            continue
        assert out[preset_id] is original, preset_id


def test_modify_seam_replaces_only_the_specified_field() -> None:
    base = _base()
    original = base[_builtin_id()]
    spec = SeamSpec(
        spec_version="0.1",
        actions=(
            SeamAction(
                op="modify_seam", preset_id=_builtin_id(), wiring="opt_in"
            ),
        ),
    )
    out = apply_spec_to_seams(spec, base)
    new = out[_builtin_id()]
    assert new.wiring == "opt_in"
    # Every other field unchanged.
    assert new.preset_id == original.preset_id
    assert new.controls_refs == original.controls_refs
    assert new.runtime_default_on == original.runtime_default_on
    assert new.supported_modes == original.supported_modes
    assert new.controls_kind == original.controls_kind


def test_modify_seam_with_no_overrides_is_noop_for_that_seam() -> None:
    # A modify_seam action with every override = None means "this seam
    # appeared in the spec but no fields were changed". The output must
    # still reuse the same base object (no surprise dataclass.replace).
    base = _base()
    spec = SeamSpec(
        spec_version="0.1",
        actions=(SeamAction(op="modify_seam", preset_id=_builtin_id()),),
    )
    out = apply_spec_to_seams(spec, base)
    assert out[_builtin_id()] is base[_builtin_id()]


def test_modify_seam_can_change_multiple_fields_at_once() -> None:
    base = _base()
    spec = SeamSpec(
        spec_version="0.1",
        actions=(
            SeamAction(
                op="modify_seam",
                preset_id=_builtin_id(),
                wiring="opt_in",
                runtime_default_on=False,
                controls_kind="evidence",
            ),
        ),
    )
    new = apply_spec_to_seams(spec, base)[_builtin_id()]
    assert new.wiring == "opt_in"
    assert new.runtime_default_on is False
    assert new.controls_kind == "evidence"


def test_add_seam_inserts_new_preset() -> None:
    base = _base()
    spec = SeamSpec(
        spec_version="0.1",
        actions=(
            SeamAction(
                op="add_seam",
                preset_id="custom:partner-approval",
                controls_refs=("partner_approval_evidence",),
                runtime_default_on=False,
                wiring="opt_in",
                controls_kind="validator",
                supported_modes=("deterministic",),
            ),
        ),
    )
    out = apply_spec_to_seams(spec, base)
    added = out["custom:partner-approval"]
    assert added.controls_refs == ("partner_approval_evidence",)
    assert added.runtime_default_on is False
    assert added.wiring == "opt_in"
    assert added.controls_kind == "validator"
    assert added.supported_modes == ("deterministic",)


def test_add_seam_defaults_supported_modes_when_omitted() -> None:
    # supported_modes is optional in SeamAction; when omitted the apply
    # function picks the PresetSeam dataclass default. Use validate-clean
    # action by including supported_modes=None.
    base = _base()
    action = SeamAction(
        op="add_seam",
        preset_id="custom:x",
        controls_refs=("ref:x",),
        runtime_default_on=True,
        wiring="opt_in",
        controls_kind="validator",
        supported_modes=None,
    )
    spec = SeamSpec(spec_version="0.1", actions=(action,))
    out = apply_spec_to_seams(spec, base)
    assert out["custom:x"].supported_modes == ("deterministic",)


def test_apply_raises_on_invalid_spec_before_mutating() -> None:
    base = _base()
    snapshot = dict(base)
    spec = SeamSpec(
        spec_version="0.1",
        actions=(SeamAction(op="modify_seam", preset_id="does-not-exist"),),
    )
    with pytest.raises(ValueError, match="not a builtin seam"):
        apply_spec_to_seams(spec, base)
    # No partial mutation.
    assert base == snapshot


def test_apply_raises_on_duplicate_preset_id() -> None:
    base = _base()
    spec = SeamSpec(
        spec_version="0.1",
        actions=(
            SeamAction(op="modify_seam", preset_id=_builtin_id(), wiring="opt_in"),
            SeamAction(op="modify_seam", preset_id=_builtin_id(), wiring="opt_out"),
        ),
    )
    with pytest.raises(ValueError, match="duplicates"):
        apply_spec_to_seams(spec, base)
