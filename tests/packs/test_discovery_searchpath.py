from pathlib import Path

from magi_agent.packs.discovery import (
    DiscoveredPack,
    default_search_bases,
    discover_pack_files,
)


def test_default_search_bases_order(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(cwd)
    bases = default_search_bases()
    # bundled firstparty dir is first, then ~/.magi/packs, then <cwd>/.magi/packs
    assert bases[-2] == home / ".magi" / "packs"
    assert bases[-1] == cwd / ".magi" / "packs"
    assert bases[0].name == "packs"  # bundled magi_agent/firstparty/packs


def test_discover_skips_missing_bases(tmp_path):
    missing = tmp_path / "nope"
    found = discover_pack_files([missing])
    assert found == []


def test_discover_finds_pack_toml_rglob(tmp_path):
    base = tmp_path / "packs"
    (base / "alpha").mkdir(parents=True)
    (base / "alpha" / "pack.toml").write_text(
        'packId="a"\ndisplayName="a"\n'
    )
    (base / "nested" / "beta").mkdir(parents=True)
    (base / "nested" / "beta" / "pack.toml").write_text(
        'packId="b"\ndisplayName="b"\n'
    )
    found = discover_pack_files([base])
    refs = sorted(d.manifest.pack_id for d in found)
    assert refs == ["a", "b"]
    assert all(isinstance(d, DiscoveredPack) for d in found)
    assert all(d.path.name == "pack.toml" for d in found)
    # pack_dir is the directory containing pack.toml (relpath base for spec files)
    assert all(d.pack_dir == d.path.parent for d in found)


def test_discover_is_deterministic_sorted(tmp_path):
    base = tmp_path / "packs"
    for name in ("c", "a", "b"):
        (base / name).mkdir(parents=True)
        (base / name / "pack.toml").write_text(f'packId="{name}"\ndisplayName="{name}"\n')
    found = discover_pack_files([base])
    assert [d.manifest.pack_id for d in found] == ["a", "b", "c"]
