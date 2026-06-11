from pathlib import Path

from magi_agent.packs.discovery import (
    DiscoveredPack,
    PacksConfig,
    load_packs_config,
    resolve_enabled_packs,
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


def test_load_packs_config_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    cfg = load_packs_config()
    assert cfg == PacksConfig()


def test_load_packs_config_reads_section(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        '[packs]\n'
        'disable = ["p.bad"]\n'
        'order = ["p.first"]\n'
        'override = ["p.user"]\n'
    )
    monkeypatch.setenv("MAGI_CONFIG", str(cfg_path))
    cfg = load_packs_config()
    assert cfg.disable == ("p.bad",)
    assert cfg.order == ("p.first",)
    assert cfg.override == ("p.user",)


def test_load_packs_config_malformed_returns_empty(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("= = not toml")
    monkeypatch.setenv("MAGI_CONFIG", str(cfg_path))
    assert load_packs_config() == PacksConfig()


def test_resolve_disables_drop_packs():
    discovered = [_disc("p.keep"), _disc("p.bad")]
    cfg = PacksConfig(disable=("p.bad",))
    result = resolve_enabled_packs(discovered, cfg)
    assert [d.manifest.pack_id for d in result] == ["p.keep"]


def test_resolve_default_disabled_pack_dropped():
    discovered = [_disc("p.on", enabled=True), _disc("p.off", enabled=False)]
    result = resolve_enabled_packs(discovered, PacksConfig())
    assert [d.manifest.pack_id for d in result] == ["p.on"]


def test_resolve_order_pins_listed_first_then_rest_sorted():
    discovered = [_disc("a"), _disc("b"), _disc("z.pinned")]
    cfg = PacksConfig(order=("z.pinned",))
    result = resolve_enabled_packs(discovered, cfg)
    assert [d.manifest.pack_id for d in result] == ["z.pinned", "a", "b"]
