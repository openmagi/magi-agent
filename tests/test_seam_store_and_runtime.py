"""PR-C2 — SeamSpec store extension + seam_for_user runtime override.

Exercises only the *store* and *runtime-lookup* layers. The HTTP endpoints
that talk to these layers live in ``test_seam_endpoint.py``.
"""

from __future__ import annotations

import json

from magi_agent.customize.preset_map import (
    PRESET_SEAMS,
    PresetSeam,
    seam_for,
    seam_for_user,
)
from magi_agent.customize.store import (
    DEFAULT_OVERRIDES,
    delete_seam_spec,
    load_overrides,
    save_overrides,
    set_seam_spec,
)


# ---------------------------------------------------------------------------
# DEFAULT_OVERRIDES — seam_specs key
# ---------------------------------------------------------------------------


def test_default_overrides_includes_empty_seam_specs() -> None:
    # OFF must be byte-identical: a fresh customize.json carries no specs.
    assert DEFAULT_OVERRIDES["verification"]["seam_specs"] == []


def test_load_overrides_normalizes_missing_seam_specs_to_empty_list(tmp_path) -> None:
    cfile = tmp_path / "customize.json"
    # An older customize.json with no seam_specs key — the load path must
    # normalize it so callers can assume the field exists.
    cfile.write_text(
        json.dumps(
            {"verification": {"recipes": [], "harness_presets": []}, "tools": {}}
        )
    )
    overrides = load_overrides(cfile)
    assert overrides["verification"]["seam_specs"] == []


# ---------------------------------------------------------------------------
# set_seam_spec / delete_seam_spec — upsert + delete by id
# ---------------------------------------------------------------------------


def _make_doc(spec_id: str, *, preset_id: str, wiring: str) -> dict:
    return {
        "id": spec_id,
        "spec_version": "0.1",
        "actions": [
            {"op": "modify_seam", "preset_id": preset_id, "wiring": wiring}
        ],
    }


def test_set_seam_spec_appends_new_doc(tmp_path) -> None:
    cfile = tmp_path / "customize.json"
    save_overrides({}, cfile)
    out = set_seam_spec(
        _make_doc("seam_a", preset_id="coding-verification", wiring="opt_in"),
        path=cfile,
    )
    specs = out["verification"]["seam_specs"]
    assert len(specs) == 1
    assert specs[0]["id"] == "seam_a"


def test_set_seam_spec_replaces_by_id(tmp_path) -> None:
    cfile = tmp_path / "customize.json"
    save_overrides({}, cfile)
    set_seam_spec(
        _make_doc("seam_a", preset_id="coding-verification", wiring="opt_in"),
        path=cfile,
    )
    out = set_seam_spec(
        _make_doc("seam_a", preset_id="coding-verification", wiring="opt_out"),
        path=cfile,
    )
    specs = out["verification"]["seam_specs"]
    assert len(specs) == 1
    assert specs[0]["actions"][0]["wiring"] == "opt_out"


def test_set_seam_spec_keeps_unrelated_specs(tmp_path) -> None:
    cfile = tmp_path / "customize.json"
    save_overrides({}, cfile)
    set_seam_spec(
        _make_doc("seam_a", preset_id="coding-verification", wiring="opt_in"),
        path=cfile,
    )
    out = set_seam_spec(
        _make_doc("seam_b", preset_id="fact-grounding", wiring="opt_out"),
        path=cfile,
    )
    ids = [s["id"] for s in out["verification"]["seam_specs"]]
    assert ids == ["seam_a", "seam_b"]


def test_delete_seam_spec_removes_only_target(tmp_path) -> None:
    cfile = tmp_path / "customize.json"
    save_overrides({}, cfile)
    set_seam_spec(
        _make_doc("seam_a", preset_id="coding-verification", wiring="opt_in"),
        path=cfile,
    )
    set_seam_spec(
        _make_doc("seam_b", preset_id="fact-grounding", wiring="opt_out"),
        path=cfile,
    )
    out = delete_seam_spec("seam_a", path=cfile)
    ids = [s["id"] for s in out["verification"]["seam_specs"]]
    assert ids == ["seam_b"]


def test_delete_seam_spec_absent_id_is_noop(tmp_path) -> None:
    cfile = tmp_path / "customize.json"
    save_overrides({}, cfile)
    set_seam_spec(
        _make_doc("seam_a", preset_id="coding-verification", wiring="opt_in"),
        path=cfile,
    )
    out = delete_seam_spec("does-not-exist", path=cfile)
    assert [s["id"] for s in out["verification"]["seam_specs"]] == ["seam_a"]


# ---------------------------------------------------------------------------
# seam_for_user — runtime layered lookup
# ---------------------------------------------------------------------------


def test_seam_for_user_with_no_docs_is_byte_identical_to_seam_for() -> None:
    for preset_id in PRESET_SEAMS:
        assert seam_for_user(preset_id) is seam_for(preset_id)
        assert seam_for_user(preset_id, spec_docs=()) is seam_for(preset_id)
        assert seam_for_user(preset_id, spec_docs=None) is seam_for(preset_id)


def test_seam_for_user_applies_modify_action() -> None:
    doc = _make_doc("seam_a", preset_id="coding-verification", wiring="opt_in")
    merged = seam_for_user("coding-verification", spec_docs=[doc])
    assert merged is not None
    assert merged.wiring == "opt_in"
    # Other fields untouched.
    builtin = seam_for("coding-verification")
    assert merged.controls_refs == builtin.controls_refs
    assert merged.runtime_default_on == builtin.runtime_default_on


def test_seam_for_user_applies_add_action_for_unknown_preset() -> None:
    add_doc = {
        "id": "seam_new",
        "spec_version": "0.1",
        "actions": [
            {
                "op": "add_seam",
                "preset_id": "custom:partner-approval",
                "controls_refs": ["partner_approval_evidence"],
                "runtime_default_on": False,
                "wiring": "opt_in",
                "controls_kind": "validator",
            }
        ],
    }
    merged = seam_for_user("custom:partner-approval", spec_docs=[add_doc])
    assert isinstance(merged, PresetSeam)
    assert merged.controls_refs == ("partner_approval_evidence",)
    assert merged.wiring == "opt_in"


def test_seam_for_user_skips_malformed_doc_without_raising() -> None:
    # An invalid doc (modify of a non-existent preset) must NOT crash lookup
    # — the UI is the surface for structural issues; the runtime stays safe.
    bad_doc = {
        "id": "seam_bad",
        "spec_version": "0.1",
        "actions": [{"op": "modify_seam", "preset_id": "never-existed"}],
    }
    good_doc = _make_doc(
        "seam_good", preset_id="coding-verification", wiring="opt_in"
    )
    merged = seam_for_user(
        "coding-verification", spec_docs=[bad_doc, good_doc]
    )
    assert merged is not None
    assert merged.wiring == "opt_in"


def test_seam_for_user_skips_non_dict_entries() -> None:
    # Defensive: a stale JSON file could carry a non-dict in the array.
    merged = seam_for_user(
        "coding-verification", spec_docs=[None, "junk", 42]  # type: ignore[list-item]
    )
    assert merged is seam_for("coding-verification")
