"""F1 firing test: deterministic_ref custom rule fires at the pre-final gate.

Proves a ``deterministic_ref`` custom rule, authored via the same persisted
``customize.json`` path the wizard / NL authoring use, actually fires at the
pre-final gate compile seam (``_apply_customize_verification``) and INJECTS its
required ref into the validator list — i.e. would block a turn that has not
produced the required evidence.

Drives the gate's decision function (``_apply_customize_verification``) end-to-
end and asserts the decision outcome on the assembled required-validators list.
No mocks on the wiring path: real store, real policy, real menu, real compile.

Pairs the firing-positive assertion with:
  * a satisfied case — the ref is already produced (already in the input list)
    so the rule does not block (no duplication, single occurrence retained); and
  * a flags-off default-OFF inert assertion — both gates closed → byte-identical.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from magi_agent.cli.real_runner import _apply_customize_verification

_REF = "evidence:test-run"


def _det_rule(ref: str = _REF, rid: str = "cr_det_ref_firing") -> dict:
    """Authoring-shape deterministic_ref rule the wizard / NL path would produce.

    Per the spec (firesAt is fixed to ``pre_final`` for deterministic_ref;
    action is ``block`` for the firing semantics under test).
    """
    return {
        "id": rid,
        "scope": "coding",
        "enabled": True,
        "what": {"kind": "deterministic_ref", "payload": {"ref": ref}},
        "firesAt": "pre_final",
        "action": "block",
    }


def _write_customize_json(path: Path, rule: dict) -> None:
    """Persist a single deterministic_ref rule into customize.json on disk.

    Mirrors what the wizard / NL authoring endpoint writes via
    ``magi_agent.customize.store.set_custom_rule`` but constructs the JSON
    document directly so this test exercises the load-from-disk path the
    runtime hits at the pre-final gate.
    """
    payload = {
        "verification": {
            "recipes": [],
            "harness_presets": [],
            "preset_overrides": {},
            "hooks": {},
            "modes": {},
            "custom_rules": [rule],
            "seam_specs": [],
        },
        "tools": {},
        "user_rules": "",
        "control_plane": {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


@pytest.fixture
def customize_file(monkeypatch, tmp_path) -> Path:
    """Per-test isolated customize.json the store will load."""
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    return cfile


# --- Firing positive: ref missing → injected into required_validators -------
def test_deterministic_ref_fires_and_injects_required_ref(
    customize_file, monkeypatch
):
    """A deterministic_ref rule with ref=evidence:test-run fires at pre-final
    and adds the ref to ``required_validators`` when the turn has not already
    produced it. This is the BLOCK path: downstream gate sees a required ref
    with no matching evidence → block.
    """
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    _write_customize_json(customize_file, _det_rule())

    # Input list deliberately does NOT contain evidence:test-run — the rule's
    # job is to ensure the pre-final gate requires it.
    out = _apply_customize_verification(["seed:ref"])

    assert _REF in out, (
        f"deterministic_ref rule did not fire — expected {_REF!r} to be "
        f"injected into required_validators; got {out!r}"
    )
    # Seed ref is preserved (compile is additive, not destructive).
    assert "seed:ref" in out
    # No duplication.
    assert out.count(_REF) == 1


# --- Negative (satisfied): ref already present → no duplication -------------
def test_deterministic_ref_satisfied_does_not_duplicate(
    customize_file, monkeypatch
):
    """When the required ref is ALREADY in the input list (i.e. a producer
    upstream already contributed it for this turn), the rule must not block via
    duplication: the ref appears exactly once. This is the PASS path.
    """
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    _write_customize_json(customize_file, _det_rule())

    out = _apply_customize_verification(["seed:ref", _REF])

    assert out.count(_REF) == 1, (
        f"satisfied ref should not duplicate; got {out!r}"
    )
    assert "seed:ref" in out


# --- Default-OFF inert (byte-identical) -------------------------------------
def test_deterministic_ref_inert_when_flags_off(customize_file, monkeypatch):
    """Master OFF + custom-rules OFF → compile is byte-identical (no injection
    despite the rule being persisted). Guards the default-OFF contract."""
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "0")
    _write_customize_json(customize_file, _det_rule())

    assert _apply_customize_verification(["seed:ref"]) == ["seed:ref"]


# --- Disabled rule does not fire even when both flags are on ----------------
def test_disabled_deterministic_ref_does_not_fire(customize_file, monkeypatch):
    """A persisted-but-disabled rule must not inject its ref. Pairs the firing
    positive: confirms the ``enabled: False`` toggle is honored at compile."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    rule = _det_rule()
    rule["enabled"] = False
    _write_customize_json(customize_file, rule)

    out = _apply_customize_verification(["seed:ref"])
    assert _REF not in out
    assert out == ["seed:ref"]
