"""F1 firing test — a persisted SeamSpec overrides runtime ``seam_for_user``.

Proves the end-to-end seam-spec wiring fires: a user-approved SeamSpec
written to ``customize.json`` and loaded via :func:`load_overrides` flows
through :func:`magi_agent.customize.preset_map.seam_for_user` and changes
the resolved :class:`PresetSeam` for the targeted preset.

Scope: this is the *firing* (smoke) check on the runtime seam-lookup layer.
The unit-level coverage of ``set_seam_spec`` / ``delete_seam_spec`` /
``seam_for_user`` lives in ``tests/test_seam_store_and_runtime.py``; this
suite exercises the persist→load→merge chain as a single observable
transition with the two gate flags flipped ON (matching the production
call-site preconditions).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from magi_agent.customize.preset_map import (
    PRESET_SEAMS,
    PresetSeam,
    seam_for,
    seam_for_user,
)
from magi_agent.customize.store import load_overrides


# ``coding-verification`` is a stable opt-out builtin from PRESET_SEAMS (see
# ``preset_map.PRESET_SEAMS``). Flipping its wiring to ``opt_in`` via a
# ``modify_seam`` action is the smallest observable override that survives the
# 3-gate validator (legal wiring, modifiable preset id, no duplicate ids).
_TARGET_PRESET = "coding-verification"


def _persisted_spec_doc(spec_id: str, *, preset_id: str, wiring: str) -> dict:
    """A ``customize.json``-shaped seam-spec doc (id + version + 1 action).

    Mirrors the shape produced by ``transport.customize`` when a user approves
    a NL→IR spec, so the loader normalization runs against the real format.
    """
    return {
        "id": spec_id,
        "spec_version": "0.1",
        "actions": [
            {"op": "modify_seam", "preset_id": preset_id, "wiring": wiring}
        ],
    }


def _write_customize_json(path: Path, *, seam_specs: list[dict]) -> None:
    """Write a minimal but loader-valid customize.json carrying ``seam_specs``."""
    payload = {
        "verification": {
            "recipes": [],
            "harness_presets": [],
            "preset_overrides": {},
            "hooks": {},
            "modes": {},
            "custom_rules": [],
            "seam_specs": seam_specs,
        },
        "tools": {},
        "user_rules": "",
        "control_plane": {},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _enable_customize_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """Match the production call-site preconditions for seam-spec lookup."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_SEAM_SPEC_ENABLED", "1")


# ---------------------------------------------------------------------------
# Sanity guard — the targeted preset must remain a known opt-out builtin so
# that flipping its wiring to opt_in is a meaningful, observable override.
# ---------------------------------------------------------------------------


def test_target_preset_is_a_known_opt_out_builtin() -> None:
    builtin = PRESET_SEAMS.get(_TARGET_PRESET)
    assert builtin is not None, f"{_TARGET_PRESET} is no longer in PRESET_SEAMS"
    assert builtin.wiring == "opt_out", (
        f"{_TARGET_PRESET} wiring changed; pick a different opt-out preset"
    )


# ---------------------------------------------------------------------------
# Positive — a persisted seam_spec overrides the builtin wiring at runtime.
# ---------------------------------------------------------------------------


def test_persisted_seam_spec_overrides_runtime_seam_for_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_customize_flags(monkeypatch)

    cfile = tmp_path / "customize.json"
    spec_doc = _persisted_spec_doc(
        "seam_firing_a", preset_id=_TARGET_PRESET, wiring="opt_in"
    )
    _write_customize_json(cfile, seam_specs=[spec_doc])

    overrides = load_overrides(cfile)
    spec_docs = overrides["verification"]["seam_specs"]
    assert len(spec_docs) == 1, "loader must surface the persisted spec verbatim"
    assert spec_docs[0]["id"] == "seam_firing_a"

    merged = seam_for_user(_TARGET_PRESET, spec_docs=spec_docs)
    builtin = seam_for(_TARGET_PRESET)

    assert merged is not None
    assert isinstance(merged, PresetSeam)
    # The targeted field reflects the override...
    assert merged.wiring == "opt_in"
    assert builtin.wiring == "opt_out"
    # ...and the un-targeted fields are inherited verbatim from the builtin
    # (proving this is a field-level override, not a whole-seam replace).
    assert merged.preset_id == builtin.preset_id
    assert merged.controls_refs == builtin.controls_refs
    assert merged.runtime_default_on == builtin.runtime_default_on
    assert merged.controls_kind == builtin.controls_kind
    assert merged.supported_modes == builtin.supported_modes


# ---------------------------------------------------------------------------
# Negative — with no persisted specs, seam_for_user returns the builtin
# unmodified (byte-identical to ``seam_for``).
# ---------------------------------------------------------------------------


def test_empty_spec_docs_returns_builtin_unmodified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_customize_flags(monkeypatch)

    cfile = tmp_path / "customize.json"
    _write_customize_json(cfile, seam_specs=[])

    overrides = load_overrides(cfile)
    spec_docs = overrides["verification"]["seam_specs"]
    assert spec_docs == []

    # ``is`` identity: with no docs the function returns the exact builtin
    # object (no defensive copy) so downstream identity comparisons stay valid.
    assert seam_for_user(_TARGET_PRESET, spec_docs=spec_docs) is seam_for(
        _TARGET_PRESET
    )
    assert seam_for_user(_TARGET_PRESET, spec_docs=[]) is seam_for(_TARGET_PRESET)
    assert seam_for_user(_TARGET_PRESET) is seam_for(_TARGET_PRESET)
