"""Pack B1 — the scaffolding engine yields a loadable pack for every provides type."""
from __future__ import annotations

import sys

import pytest

from magi_agent.packs.loader import RecordingSink, load_from_bases
from magi_agent.packs.scaffold import PACK_TYPES, scaffold_pack


@pytest.mark.parametrize("ptype", PACK_TYPES)
def test_scaffolded_pack_loads_with_zero_syspath_setup(ptype, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(sys, "path", [*sys.path])  # revert loader auto-injection
    # Unique name per type: sys.modules caches by top-level dir name.
    meta = scaffold_pack(ptype, f"demo-{ptype.replace('_', '-')}", tmp_path / "packs")

    result, _catalog = load_from_bases([tmp_path / "packs"], RecordingSink())
    primitives = {(p.type, p.ref): p for p in result.primitives}
    assert (ptype, meta.ref) in primitives, sorted(primitives)
    if ptype == "recipe":
        assert primitives[(ptype, meta.ref)].spec_path is not None
        assert meta.impl_path is None and meta.spec_path is not None
    else:
        assert callable(primitives[(ptype, meta.ref)].impl)
        assert meta.impl_path is not None and meta.spec_path is None
    assert meta.pack_toml.is_file() and meta.test_path.is_file()


def test_scaffold_rejects_unknown_type(tmp_path) -> None:
    with pytest.raises(ValueError, match="unknown pack type"):
        scaffold_pack("widget", "x", tmp_path / "packs")


def test_scaffold_rejects_existing_dir(tmp_path) -> None:
    scaffold_pack("tool", "dup-name", tmp_path / "packs")
    with pytest.raises(ValueError, match="already exists"):
        scaffold_pack("tool", "dup-name", tmp_path / "packs")


def test_module_name_sanitization_and_validator_ref(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(sys, "path", [*sys.path])
    meta = scaffold_pack("validator", "My Fancy-Check", tmp_path / "packs")
    assert meta.pack_dir.name == "my_fancy_check"
    assert meta.ref == "verifier:myFancyCheck@1"


def test_scaffold_code_recipe_emits_callable_and_registers_when_flag_on(
    tmp_path, monkeypatch
) -> None:
    """PR4: `--code` recipe scaffold emits a provide_recipe callable + spec_callable;
    it loads + registers only under MAGI_RECIPE_AS_CODE_ENABLED, and uses an ext.
    packId so the untrusted trust boundary admits it."""
    from magi_agent.packs.manifest import load_manifest_from_toml
    from magi_agent.packs.registries import PackRegistries, project_into_registries

    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(sys, "path", [*sys.path])
    meta = scaffold_pack("recipe", "code-flow", tmp_path / "packs", code=True)

    assert meta.impl_path is not None and meta.spec_path is None
    manifest = load_manifest_from_toml(meta.pack_toml)
    entry = manifest.provides[0]
    assert entry.spec_callable == "code_flow.impl:provide_recipe"
    assert manifest.pack_id.startswith("ext.")

    # OFF: dropped at load time (callable never imported).
    monkeypatch.setenv("MAGI_RECIPE_AS_CODE_ENABLED", "0")
    off_result, _ = load_from_bases([tmp_path / "packs"], RecordingSink())
    assert ("recipe", meta.ref) not in {(p.type, p.ref) for p in off_result.primitives}

    # ON: loaded + projected into the live recipe registry.
    monkeypatch.setenv("MAGI_RECIPE_AS_CODE_ENABLED", "1")
    on_result, _ = load_from_bases([tmp_path / "packs"], RecordingSink())
    report = project_into_registries(on_result.primitives, PackRegistries())
    assert meta.ref in report.registered


def test_scaffold_code_flag_rejected_for_non_recipe(tmp_path) -> None:
    with pytest.raises(ValueError, match="only valid for the 'recipe'"):
        scaffold_pack("tool", "x", tmp_path / "packs", code=True)
