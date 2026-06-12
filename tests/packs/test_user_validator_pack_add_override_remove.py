"""Task 3.4 — a USER pack can add / override / remove (forbid) a validator with
NO first-party privilege (§1), through the same loader/catalog/registry path.

Adapted to the real Phase-1/2 ABI:
  * discovery   = ``discover_pack_files(bases)`` + ``resolve_enabled_packs(disc, cfg)``;
  * override    = load order (last pack wins), resolved by ``resolve_enabled_packs``
    which orders by pack_id (so a ``user.*`` id sorts after ``openmagi.*``) and the
    ``RegistryRegistrationSink`` applies last-wins ``override=True``;
  * forbid      = ``config.toml [packs] disable = ["<pack_id>"]`` (by pack_id — there
    is no by-ref ``forbid`` knob in ``PacksConfig``; the doc anticipated this re-grep
    adaptation);
  * pack.toml   = top-level ``packId``/``displayName``; refs use the ``verifier:`` prefix.
"""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.catalog_build import build_catalog
from magi_agent.packs.context import PrimitiveType, SessionReadView, ValidatorCtx
from magi_agent.packs.discovery import (
    discover_pack_files,
    load_packs_config,
    resolve_enabled_packs,
)
from magi_agent.packs.loader import load_packs
from magi_agent.packs.registries import PrimitiveRegistry, RegistryRegistrationSink

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"
_FP_REF = "verifier:sourceOpened@1"


def _write_user_pack(
    root: Path, *, pack_id: str, ref: str, passed: bool, name: str
) -> Path:
    pack_dir = root / name
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "impl.py").write_text(
        "from magi_agent.packs.context import ValidatorCtx\n"
        f"_REF = {ref!r}\n"
        "def user_validator(ctx: ValidatorCtx):\n"
        f"    ctx.emit(passed={passed!r})\n"
        "    return ctx.verdict()\n"
    )
    (pack_dir / "pack.toml").write_text(
        f"packId = {pack_id!r}\n"
        f"displayName = {pack_id!r}\n"
        "version = \"0.0.1\"\n\n"
        "[[provides]]\n"
        "type = \"validator\"\n"
        f"ref = {ref!r}\n"
        f"impl = \"{name}.impl:user_validator\"\n"
    )
    return pack_dir


def _load_registry(bases: list[Path]) -> tuple[PrimitiveRegistry, object]:
    discovered = discover_pack_files(bases)
    enabled = resolve_enabled_packs(discovered, load_packs_config())
    registry = PrimitiveRegistry()
    result = load_packs(enabled, RegistryRegistrationSink(registry))
    catalog = build_catalog(result.primitives)
    return registry, catalog


def test_user_pack_ADDS_a_second_validator(tmp_path, monkeypatch) -> None:
    user_root = tmp_path / "user_packs"
    _write_user_pack(
        user_root, pack_id="user.quote", ref="verifier:userQuote@1",
        passed=True, name="user_quote_pack",
    )
    monkeypatch.syspath_prepend(str(user_root))
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))

    registry, catalog = _load_registry([_FIRST_PARTY_ROOT, user_root])
    assert _FP_REF in catalog.validator_refs            # first-party still present
    assert "verifier:userQuote@1" in catalog.validator_refs  # user ADD works

    impl = registry.resolve("verifier:userQuote@1", ptype=PrimitiveType.VALIDATOR)
    assert impl is not None


def test_user_pack_OVERRIDES_a_first_party_ref(tmp_path, monkeypatch) -> None:
    user_root = tmp_path / "user_packs"
    # Same ref as the first-party validator; pack_id "user.*" sorts AFTER
    # "openmagi.*" so the user impl wins by load order (last-wins).
    _write_user_pack(
        user_root, pack_id="user.override-source", ref=_FP_REF,
        passed=False, name="user_override_pack",
    )
    monkeypatch.syspath_prepend(str(user_root))
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))

    registry, _ = _load_registry([_FIRST_PARTY_ROOT, user_root])
    impl = registry.resolve(_FP_REF, ptype=PrimitiveType.VALIDATOR)
    session = SessionReadView(invocation_id="i", agent_name="a", turn_index=0)
    # First-party returns passed=True when observed; the user override always False.
    verdict = impl(ValidatorCtx(ref=_FP_REF, artifact={"observedRefs": [_FP_REF]}, session=session))
    assert verdict.passed is False  # user impl WON — no first-party privilege


def test_user_pack_REMOVES_forbids_a_first_party_ref(tmp_path, monkeypatch) -> None:
    user_root = tmp_path / "user_packs"
    user_root.mkdir(parents=True, exist_ok=True)
    config_path = tmp_path / "config.toml"
    # forbid knob = [packs] disable keyed by PACK_ID (PacksConfig has no by-ref forbid).
    config_path.write_text('[packs]\ndisable = ["openmagi.source-opened"]\n')
    monkeypatch.setenv("MAGI_CONFIG", str(config_path))

    registry, catalog = _load_registry([_FIRST_PARTY_ROOT, user_root])
    assert _FP_REF not in catalog.validator_refs  # forbidden out
    import pytest
    with pytest.raises(KeyError):
        registry.resolve(_FP_REF, ptype=PrimitiveType.VALIDATOR)
