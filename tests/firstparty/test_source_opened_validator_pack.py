"""Task 3.1 — the bundled first-party validator pack on disk + typed impl.

Adapted to the REAL Phase-1/Phase-2 ABI (the plan doc's snapshots drifted):
  * pack.toml is top-level ``packId``/``displayName`` (no ``[pack]`` table) — see
    ``tests/packs/fixtures/example_pack/pack.toml``.
  * ``ValidatorCtx`` takes ``ref``/``artifact``/``session`` and emits a verdict via
    ``ctx.emit(passed=...)`` / ``ctx.verdict()`` (it does NOT take ``required_ref``/
    ``observed_public_refs`` nor return a ``ValidatorResult``).
  * The validator ref uses the live public-ref prefix ``verifier:`` so it can reach
    the real ``cli/engine.py`` enforce path (``validator:`` is NOT a recognized
    public-ref prefix in ``harness/verifier_bus._PUBLIC_REF_PREFIXES``).
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import magi_agent

_PACK = (
    Path(magi_agent.__file__).parent
    / "firstparty"
    / "packs"
    / "source_opened_validator"
    / "pack.toml"
)

_REF = "verifier:sourceOpened@1"


def test_first_party_validator_pack_declares_validator_statically() -> None:
    raw = tomllib.loads(_PACK.read_text())
    assert raw["packId"] == "openmagi.source-opened"
    provides = raw["provides"]
    assert len(provides) == 1
    entry = provides[0]
    assert entry["type"] == "validator"
    assert entry["ref"] == _REF
    assert entry["impl"] == (
        "magi_agent.firstparty.packs.source_opened_validator.impl:source_opened_validator"
    )


def test_first_party_validator_impl_is_importable_and_typed() -> None:
    from magi_agent.firstparty.packs.source_opened_validator.impl import (
        source_opened_validator,
    )
    from magi_agent.packs.context import SessionReadView, ValidatorCtx

    session = SessionReadView(invocation_id="i", agent_name="a", turn_index=0)
    # artifact carries the observed public refs for this turn (verdict is supported
    # iff the required ref was observed).
    ctx = ValidatorCtx(
        ref=_REF,
        artifact={"observedRefs": [_REF]},
        session=session,
    )
    verdict = source_opened_validator(ctx)
    assert verdict is not None
    assert verdict.ref == _REF
    assert verdict.passed is True

    missing_ctx = ValidatorCtx(ref=_REF, artifact={"observedRefs": []}, session=session)
    assert source_opened_validator(missing_ctx).passed is False
