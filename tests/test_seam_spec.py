"""PR-C1 — SeamSpec dataclass + structural validator.

The compiler that produces a SeamSpec is tested separately
(``tests/test_seam_compiler.py``). This module covers only the *deterministic*
shape: parse from JSON, validate the resulting IR. No model calls.
"""

from __future__ import annotations

import pytest

from magi_agent.customize.seam_spec import (
    LEGAL_CONTROLS_KINDS,
    LEGAL_OPS,
    LEGAL_SUPPORTED_MODES,
    LEGAL_WIRINGS,
    SPEC_VERSION,
    SeamAction,
    SeamSpec,
    modifiable_preset_ids,
    parse_spec,
    validate_spec,
)


# ---------------------------------------------------------------------------
# Constants — sanity guards so the legal sets stay in lock-step with PresetSeam
# ---------------------------------------------------------------------------


def test_legal_wirings_match_preset_seam_field() -> None:
    assert LEGAL_WIRINGS == frozenset({"opt_in", "opt_out"})


def test_legal_controls_kinds_match_preset_seam_field() -> None:
    assert LEGAL_CONTROLS_KINDS == frozenset({"validator", "evidence"})


def test_legal_ops_are_add_and_modify() -> None:
    assert LEGAL_OPS == frozenset({"add_seam", "modify_seam"})


def test_legal_supported_modes_include_deterministic_and_llm() -> None:
    # Every supported_modes string carried by a builtin seam must be listed.
    from magi_agent.customize.preset_map import PRESET_SEAMS

    seen = {m for seam in PRESET_SEAMS.values() for m in seam.supported_modes}
    assert seen.issubset(LEGAL_SUPPORTED_MODES), (
        f"PRESET_SEAMS uses modes {seen - LEGAL_SUPPORTED_MODES} not allow-listed in seam_spec"
    )


def test_modifiable_preset_ids_sourced_from_preset_seams() -> None:
    from magi_agent.customize.preset_map import PRESET_SEAMS

    assert modifiable_preset_ids() == frozenset(PRESET_SEAMS.keys())


# ---------------------------------------------------------------------------
# parse_spec — shape errors raise; semantic errors do not
# ---------------------------------------------------------------------------


def test_parse_spec_empty_actions_round_trip() -> None:
    spec = parse_spec({"spec_version": "0.1", "actions": []})
    assert spec == SeamSpec(spec_version="0.1", actions=())


def test_parse_spec_missing_version_defaults() -> None:
    spec = parse_spec({"actions": []})
    assert spec.spec_version == SPEC_VERSION


def test_parse_spec_rejects_non_dict_root() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        parse_spec([1, 2, 3])  # type: ignore[arg-type]


def test_parse_spec_rejects_non_list_actions() -> None:
    with pytest.raises(ValueError, match="actions"):
        parse_spec({"actions": {"not": "a list"}})


def test_parse_spec_rejects_action_missing_op() -> None:
    with pytest.raises(ValueError, match=r"action\[0\]\.op"):
        parse_spec({"actions": [{"preset_id": "x"}]})


def test_parse_spec_rejects_action_missing_preset_id() -> None:
    with pytest.raises(ValueError, match=r"action\[0\]\.preset_id"):
        parse_spec({"actions": [{"op": "add_seam"}]})


def test_parse_spec_rejects_non_list_controls_refs() -> None:
    with pytest.raises(ValueError, match=r"controls_refs must be a list"):
        parse_spec(
            {
                "actions": [
                    {
                        "op": "add_seam",
                        "preset_id": "x",
                        "controls_refs": "ref:single-as-string",
                    }
                ]
            }
        )


def test_parse_spec_rejects_non_bool_runtime_default_on() -> None:
    with pytest.raises(ValueError, match=r"runtime_default_on must be a bool"):
        parse_spec(
            {
                "actions": [
                    {"op": "add_seam", "preset_id": "x", "runtime_default_on": 1}
                ]
            }
        )


def test_parse_spec_keeps_unknown_op_for_validate_to_report() -> None:
    # Shape parse must not raise on semantic errors — validate_spec is the
    # single surface that aggregates and reports them.
    spec = parse_spec({"actions": [{"op": "delete_seam", "preset_id": "x"}]})
    assert spec.actions[0].op == "delete_seam"


def test_parse_spec_collapses_controls_refs_to_string_tuple() -> None:
    spec = parse_spec(
        {
            "actions": [
                {
                    "op": "add_seam",
                    "preset_id": "x",
                    "controls_refs": ["ref:a", "ref:b", 123],
                }
            ]
        }
    )
    assert spec.actions[0].controls_refs == ("ref:a", "ref:b", "123")


# ---------------------------------------------------------------------------
# validate_spec — every issue surfaces; no early-exit
# ---------------------------------------------------------------------------


def _builtin_id() -> str:
    return "coding-verification"  # a stable PRESET_SEAMS entry


def test_validate_clean_modify_returns_no_issues() -> None:
    spec = SeamSpec(
        spec_version=SPEC_VERSION,
        actions=(SeamAction(op="modify_seam", preset_id=_builtin_id(), wiring="opt_in"),),
    )
    assert validate_spec(spec) == []


def test_validate_clean_add_returns_no_issues() -> None:
    spec = SeamSpec(
        spec_version=SPEC_VERSION,
        actions=(
            SeamAction(
                op="add_seam",
                preset_id="custom:partner-approval",
                controls_refs=("partner_approval_evidence",),
                runtime_default_on=False,
                wiring="opt_in",
                controls_kind="validator",
            ),
        ),
    )
    assert validate_spec(spec) == []


def test_validate_unknown_op_reports() -> None:
    spec = SeamSpec(
        spec_version=SPEC_VERSION,
        actions=(SeamAction(op="delete_seam", preset_id=_builtin_id()),),
    )
    issues = validate_spec(spec)
    assert any("op='delete_seam'" in i for i in issues)


def test_validate_modify_unknown_preset_reports() -> None:
    spec = SeamSpec(
        spec_version=SPEC_VERSION,
        actions=(SeamAction(op="modify_seam", preset_id="does-not-exist"),),
    )
    issues = validate_spec(spec)
    assert any("not a builtin seam" in i for i in issues)


def test_validate_add_existing_preset_reports() -> None:
    spec = SeamSpec(
        spec_version=SPEC_VERSION,
        actions=(
            SeamAction(
                op="add_seam",
                preset_id=_builtin_id(),
                controls_refs=("x",),
                runtime_default_on=True,
                wiring="opt_in",
                controls_kind="validator",
            ),
        ),
    )
    issues = validate_spec(spec)
    assert any("already exists in PRESET_SEAMS" in i for i in issues)


def test_validate_add_missing_required_fields_reports_each() -> None:
    spec = SeamSpec(
        spec_version=SPEC_VERSION,
        actions=(SeamAction(op="add_seam", preset_id="custom:x"),),
    )
    issues = validate_spec(spec)
    # Every missing required field gets its own issue, not just the first.
    for required in ("controls_refs", "runtime_default_on", "wiring", "controls_kind"):
        assert any(f"requires '{required}'" in i for i in issues), required


def test_validate_illegal_wiring_reports() -> None:
    spec = SeamSpec(
        spec_version=SPEC_VERSION,
        actions=(
            SeamAction(op="modify_seam", preset_id=_builtin_id(), wiring="opt_maybe"),
        ),
    )
    issues = validate_spec(spec)
    assert any("wiring='opt_maybe'" in i for i in issues)


def test_validate_illegal_controls_kind_reports() -> None:
    spec = SeamSpec(
        spec_version=SPEC_VERSION,
        actions=(
            SeamAction(
                op="modify_seam", preset_id=_builtin_id(), controls_kind="hint"
            ),
        ),
    )
    issues = validate_spec(spec)
    assert any("controls_kind='hint'" in i for i in issues)


def test_validate_empty_supported_modes_reports() -> None:
    spec = SeamSpec(
        spec_version=SPEC_VERSION,
        actions=(
            SeamAction(op="modify_seam", preset_id=_builtin_id(), supported_modes=()),
        ),
    )
    issues = validate_spec(spec)
    assert any("supported_modes must be non-empty" in i for i in issues)


def test_validate_unknown_supported_mode_reports() -> None:
    spec = SeamSpec(
        spec_version=SPEC_VERSION,
        actions=(
            SeamAction(
                op="modify_seam",
                preset_id=_builtin_id(),
                supported_modes=("deterministic", "magic"),
            ),
        ),
    )
    issues = validate_spec(spec)
    assert any("contains 'magic'" in i for i in issues)


def test_validate_duplicate_preset_id_reports() -> None:
    spec = SeamSpec(
        spec_version=SPEC_VERSION,
        actions=(
            SeamAction(op="modify_seam", preset_id=_builtin_id(), wiring="opt_in"),
            SeamAction(op="modify_seam", preset_id=_builtin_id(), wiring="opt_out"),
        ),
    )
    issues = validate_spec(spec)
    assert any("duplicates action[0]" in i for i in issues)


def test_validate_reports_all_issues_no_early_exit() -> None:
    # Construct a spec with three independently-broken actions to confirm
    # validate_spec aggregates rather than short-circuiting.
    spec = SeamSpec(
        spec_version=SPEC_VERSION,
        actions=(
            SeamAction(op="bogus", preset_id="custom:a", wiring="opt_in"),
            SeamAction(op="modify_seam", preset_id="never-existed"),
            SeamAction(
                op="add_seam", preset_id="custom:b", wiring="opt_unknown"
            ),
        ),
    )
    issues = validate_spec(spec)
    assert any("op='bogus'" in i for i in issues)
    assert any("not a builtin seam" in i for i in issues)
    assert any("wiring='opt_unknown'" in i for i in issues)
