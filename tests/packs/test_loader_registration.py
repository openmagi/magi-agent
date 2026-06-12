from pathlib import Path

import pytest

from magi_agent.packs.discovery import DiscoveredPack
from magi_agent.packs.loader import (
    LoadedPrimitive,
    RecordingSink,
    lazy_import_symbol,
    load_packs,
)
from magi_agent.packs.manifest import PackManifest


# A real, importable target for the lazy-import test.
def _sentinel_impl():  # noqa: D401 - test fixture symbol
    return "ok"


def test_lazy_import_symbol_resolves_module_colon_symbol():
    sym = lazy_import_symbol(f"{__name__}:_sentinel_impl")
    assert sym is _sentinel_impl
    assert sym() == "ok"


def test_lazy_import_symbol_bad_form_raises():
    with pytest.raises(ValueError):
        lazy_import_symbol("no_colon")


def test_lazy_import_symbol_missing_module_raises():
    with pytest.raises(ImportError):
        lazy_import_symbol("definitely.not.a.module:thing")


def _disc(pack_id: str, provides: list[dict]) -> DiscoveredPack:
    return DiscoveredPack(
        path=Path(f"/tmp/{pack_id}/pack.toml"),
        pack_dir=Path(f"/tmp/{pack_id}"),
        manifest=PackManifest.model_validate(
            {"packId": pack_id, "displayName": pack_id, "provides": provides}
        ),
    )


def test_load_packs_registers_code_primitive_with_impl():
    disc = _disc(
        "p.tools",
        [{"type": "tool", "ref": "Sentinel", "impl": f"{__name__}:_sentinel_impl"}],
    )
    sink = RecordingSink()
    load_packs([disc], sink)
    assert len(sink.registered) == 1
    prim = sink.registered[0]
    assert isinstance(prim, LoadedPrimitive)
    assert prim.type == "tool"
    assert prim.ref == "Sentinel"
    assert prim.impl is _sentinel_impl     # lazily imported
    assert prim.spec_path is None
    assert prim.pack_id == "p.tools"


def test_load_packs_recipe_registers_resolved_spec_path_no_import(tmp_path):
    disc = DiscoveredPack(
        path=tmp_path / "p.rec" / "pack.toml",
        pack_dir=tmp_path / "p.rec",
        manifest=PackManifest.model_validate(
            {
                "packId": "p.rec",
                "displayName": "p.rec",
                "provides": [
                    {"type": "recipe", "ref": "r@1", "spec": "recipes/r.toml"}
                ],
            }
        ),
    )
    sink = RecordingSink()
    load_packs([disc], sink)
    prim = sink.registered[0]
    assert prim.impl is None
    assert prim.spec_path == (tmp_path / "p.rec" / "recipes" / "r.toml").resolve()


def test_load_packs_last_pack_wins_on_colliding_ref():
    a = _disc("p.a", [{"type": "tool", "ref": "Dup", "impl": f"{__name__}:_sentinel_impl"}])
    b = _disc("p.b", [{"type": "tool", "ref": "Dup", "impl": f"{__name__}:_sentinel_impl"}])
    sink = RecordingSink()
    result = load_packs([a, b], sink)
    # both registrations are sent to the sink in order; the override map records winner
    assert result.overridden == {("tool", "Dup"): ("p.a", "p.b")}
    assert sink.registered[-1].pack_id == "p.b"
