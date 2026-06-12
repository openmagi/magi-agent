"""Guard: every runtime non-.py asset under magi_agent/firstparty/packs/ must be
covered by a [tool.setuptools.package-data] glob, or it vanishes from the wheel
and the pack loader discovers ZERO bundled packs (empty control plane).

This is a STATIC coverage check (fast, deterministic): it proves the
package-data patterns cover the assets on disk. The end-to-end proof that
setuptools actually ships them lives in the wheel-build verification recorded in
the fix commit; this test prevents the *coverage* from regressing when a new
bundled pack or asset type is added.
"""

from __future__ import annotations

import fnmatch
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PKG_ROOT = _REPO_ROOT / "magi_agent"
_FIRSTPARTY_PACKS = _PKG_ROOT / "firstparty" / "packs"


def _package_data_globs() -> list[str]:
    data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text())
    return list(data["tool"]["setuptools"]["package-data"]["magi_agent"])


def _runtime_pack_assets() -> list[Path]:
    """Non-.py files under firstparty/packs that the loader/catalog READ at
    runtime (pack.toml manifests + declarative recipe/spec files). Excludes
    docs (*.md like MIGRATION.md) and pycache — those are not loaded."""
    assets: list[Path] = []
    for path in _FIRSTPARTY_PACKS.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix in {".py", ".pyc", ".md"}:
            continue
        if "__pycache__" in path.parts:
            continue
        assets.append(path)
    return assets


def test_every_firstparty_pack_runtime_asset_is_packaged() -> None:
    globs = _package_data_globs()
    assets = _runtime_pack_assets()
    # Sanity: there ARE bundled packs to package (catches a wrong path).
    assert assets, f"no runtime pack assets found under {_FIRSTPARTY_PACKS}"

    uncovered: list[str] = []
    for asset in assets:
        rel = asset.relative_to(_PKG_ROOT).as_posix()  # package-data is pkg-relative
        if not any(fnmatch.fnmatch(rel, pat) for pat in globs):
            uncovered.append(rel)

    assert not uncovered, (
        "These first-party pack assets are NOT covered by any "
        "[tool.setuptools.package-data] glob and would be dropped from the "
        f"wheel (empty control plane on pip installs): {uncovered}"
    )


def test_pack_toml_manifests_exist_for_every_bundled_pack() -> None:
    """Every bundled pack dir (has an impl.py or __init__-only) must ship a
    pack.toml — the loader keys discovery on pack.toml, so a dir without one is
    invisible regardless of packaging."""
    pack_dirs = {
        p.parent
        for p in _FIRSTPARTY_PACKS.rglob("*.py")
        if p.name in {"impl.py", "__init__.py"} and p.parent != _FIRSTPARTY_PACKS
    }
    missing = sorted(
        str(d.relative_to(_FIRSTPARTY_PACKS))
        for d in pack_dirs
        if not (d / "pack.toml").is_file()
    )
    assert not missing, f"bundled pack dirs missing pack.toml: {missing}"
