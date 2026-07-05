"""Dashboard install/remove overrides (packs-state.json) merged into the
effective packs config, so a remove/install takes real runtime effect."""
from pathlib import Path

from magi_agent.packs.discovery import (
    DiscoveredPack,
    load_packs_config,
    load_packs_runtime_state,
    resolve_enabled_packs,
    set_pack_runtime_state,
)
from magi_agent.packs.manifest import PackManifest


def _disc(pack_id: str, enabled: bool = True) -> DiscoveredPack:
    return DiscoveredPack(
        path=Path(f"/tmp/{pack_id}/pack.toml"),
        pack_dir=Path(f"/tmp/{pack_id}"),
        manifest=PackManifest.model_validate(
            {"packId": pack_id, "displayName": pack_id, "defaultEnabled": enabled}
        ),
    )


def _use_config(tmp_path, monkeypatch) -> None:
    # packs-state.json is a sibling of config.toml, so pointing MAGI_CONFIG at a
    # tmp path isolates both the config and the override sidecar.
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))


# --- state file read/write ---


def test_runtime_state_missing_is_empty(tmp_path, monkeypatch):
    _use_config(tmp_path, monkeypatch)
    assert load_packs_runtime_state() == {}


def test_set_and_load_roundtrip(tmp_path, monkeypatch):
    _use_config(tmp_path, monkeypatch)
    set_pack_runtime_state("p.foo", False)
    set_pack_runtime_state("p.bar", True)
    assert load_packs_runtime_state() == {"p.foo": False, "p.bar": True}
    assert (tmp_path / "packs-state.json").exists()


def test_set_overwrites_prior_decision(tmp_path, monkeypatch):
    _use_config(tmp_path, monkeypatch)
    set_pack_runtime_state("p.foo", False)  # removed
    set_pack_runtime_state("p.foo", True)  # re-installed
    assert load_packs_runtime_state() == {"p.foo": True}


def test_malformed_state_is_empty(tmp_path, monkeypatch):
    _use_config(tmp_path, monkeypatch)
    (tmp_path / "packs-state.json").write_text("{ not json")
    assert load_packs_runtime_state() == {}


def test_non_bool_values_ignored(tmp_path, monkeypatch):
    _use_config(tmp_path, monkeypatch)
    (tmp_path / "packs-state.json").write_text('{"packs": {"p.a": "yes", "p.b": true}}')
    assert load_packs_runtime_state() == {"p.b": True}


# --- merge into effective load_packs_config ---


def test_remove_adds_to_disable(tmp_path, monkeypatch):
    _use_config(tmp_path, monkeypatch)
    set_pack_runtime_state("p.gone", False)
    cfg = load_packs_config()
    assert "p.gone" in cfg.disable


def test_removed_pack_is_dropped_end_to_end(tmp_path, monkeypatch):
    _use_config(tmp_path, monkeypatch)
    set_pack_runtime_state("p.gone", False)
    discovered = [_disc("p.keep"), _disc("p.gone")]
    result = resolve_enabled_packs(discovered, load_packs_config())
    assert [d.manifest.pack_id for d in result] == ["p.keep"]


def test_install_reenables_default_off_pack(tmp_path, monkeypatch):
    # A default_enabled=False pack is dropped by default; installing it via the
    # dashboard adds it to order, which re-enables it.
    _use_config(tmp_path, monkeypatch)
    discovered = [_disc("p.on", enabled=True), _disc("p.opt", enabled=False)]
    before = resolve_enabled_packs(discovered, load_packs_config())
    assert "p.opt" not in [d.manifest.pack_id for d in before]

    set_pack_runtime_state("p.opt", True)
    after = resolve_enabled_packs(discovered, load_packs_config())
    assert "p.opt" in [d.manifest.pack_id for d in after]


def test_install_overrides_config_toml_disable(tmp_path, monkeypatch):
    # config.toml disables p.x; the dashboard install must win (drop from disable).
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('[packs]\ndisable = ["p.x"]\n')
    monkeypatch.setenv("MAGI_CONFIG", str(cfg_path))
    assert "p.x" in load_packs_config().disable  # baseline: file disables it

    set_pack_runtime_state("p.x", True)
    cfg = load_packs_config()
    assert "p.x" not in cfg.disable
    discovered = [_disc("p.x")]
    result = resolve_enabled_packs(discovered, cfg)
    assert [d.manifest.pack_id for d in result] == ["p.x"]


def test_no_override_returns_base_config(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('[packs]\ndisable = ["p.bad"]\n')
    monkeypatch.setenv("MAGI_CONFIG", str(cfg_path))
    cfg = load_packs_config()
    assert cfg.disable == ("p.bad",)
