"""Pack B3 — the committed external-shaped example pack loads end-to-end.

Proves the full stranger path: a pack outside magi_agent.*, impl module path
relative to the pack dir ("review_guard.impl:..."), loaded with zero sys.path
setup, projecting a custom validator + a custom callback into the live
registries with no first-party privilege — and, per the §1 walkthrough, that
the template ADDS alongside first-party and a copy of it OVERRIDES a
first-party ref (last pack in resolved order wins).
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import magi_agent
from magi_agent.packs.context import SessionReadView, ValidatorCtx
from magi_agent.packs.loader import RecordingSink, load_from_bases
from magi_agent.packs.registries import PackRegistries, project_into_registries

_EXAMPLES_BASE = Path(__file__).resolve().parents[2] / "examples" / "packs"
_FIRST_PARTY_BASE = Path(magi_agent.__file__).resolve().parent / "firstparty" / "packs"
_VALIDATOR_REF = "verifier:noTodoLeft@1"
_CALLBACK_REF = "review-guard-audit"
_FP_VALIDATOR_REF = "verifier:sourceOpened@1"
_FP_CALLBACK_REF = "turn-audit"


def test_example_pack_loads_and_projects(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(sys, "path", [*sys.path])  # revert loader auto-injection

    result, catalog = load_from_bases([_EXAMPLES_BASE], RecordingSink())
    primitives = {(p.type, p.ref): p for p in result.primitives}
    assert ("validator", _VALIDATOR_REF) in primitives
    assert ("callback", _CALLBACK_REF) in primitives
    # External shape: the impls live in the pack's own module, NOT magi_agent.*.
    assert primitives[("validator", _VALIDATOR_REF)].impl.__module__ == "review_guard.impl"
    assert _VALIDATOR_REF in catalog.validator_refs
    assert _CALLBACK_REF in catalog.plugin_refs

    registries = PackRegistries()
    report = project_into_registries(result.primitives, registries)
    assert _CALLBACK_REF in report.registered
    assert registries.hooks_handler(_CALLBACK_REF) is not None


def test_example_validator_passes_and_fails(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(sys, "path", [*sys.path])

    result, _catalog = load_from_bases([_EXAMPLES_BASE], RecordingSink())
    impl = next(p.impl for p in result.primitives if p.ref == _VALIDATOR_REF)
    session = SessionReadView(invocation_id="i", agent_name="a", turn_index=0)
    ok = impl(ValidatorCtx(ref=_VALIDATOR_REF,
                           artifact={"summary": "all clean"}, session=session))
    bad = impl(ValidatorCtx(ref=_VALIDATOR_REF,
                            artifact={"summary": "TODO: finish this"}, session=session))
    assert ok.passed is True
    assert bad.passed is False and bad.detail is not None


def test_example_pack_adds_alongside_first_party(tmp_path, monkeypatch) -> None:
    """ADD (§1): loading [first-party base, examples base] lands BOTH ref sets."""
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(sys, "path", [*sys.path])

    _result, catalog = load_from_bases(
        [_FIRST_PARTY_BASE, _EXAMPLES_BASE], RecordingSink()
    )
    assert _FP_VALIDATOR_REF in catalog.validator_refs  # first-party intact
    assert _VALIDATOR_REF in catalog.validator_refs  # example ADDed
    assert _FP_CALLBACK_REF in catalog.plugin_refs
    assert _CALLBACK_REF in catalog.plugin_refs


def test_template_copy_overrides_a_first_party_ref(tmp_path, monkeypatch) -> None:
    """OVERRIDE (the §1 walkthrough a stranger follows): copy the template into a
    user pack root, re-point its validator ref at a first-party ref — the user
    base loads after the bundled base, so the copied example impl wins the ref
    (no first-party privilege)."""
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(sys, "path", [*sys.path])

    user_root = tmp_path / "user_packs"
    pack_dir = user_root / "my_review_guard"
    shutil.copytree(
        _EXAMPLES_BASE / "review_guard",
        pack_dir,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    manifest = (
        (pack_dir / "pack.toml")
        .read_text()
        .replace('packId = "examples.review-guard"', 'packId = "user.my-review-guard"')
        .replace("review_guard.impl:", "my_review_guard.impl:")
        .replace(f'ref = "{_VALIDATOR_REF}"', f'ref = "{_FP_VALIDATOR_REF}"')
    )
    (pack_dir / "pack.toml").write_text(manifest)

    result, _catalog = load_from_bases([_FIRST_PARTY_BASE, user_root], RecordingSink())
    assert result.overridden[("validator", _FP_VALIDATOR_REF)] == (
        "openmagi.source-opened",
        "user.my-review-guard",
    )
    # Last-wins winner — dict comprehension keeps the LAST primitive per
    # (type, ref), the loader's own dedup convention.
    winners = {(p.type, p.ref): p for p in result.primitives}
    impl = winners[("validator", _FP_VALIDATOR_REF)].impl
    session = SessionReadView(invocation_id="i", agent_name="a", turn_index=0)
    verdict = impl(ValidatorCtx(
        ref=_FP_VALIDATOR_REF,
        # First-party would PASS this artifact (its ref IS observed); the
        # example's no-TODO check fails it — proving the template impl won.
        artifact={"observedRefs": [_FP_VALIDATOR_REF], "summary": "TODO: not done"},
        session=session,
    ))
    assert verdict.passed is False
