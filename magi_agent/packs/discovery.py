"""Pack discovery (D1): resolve search-path bases and rglob ``pack.toml``.

Search path (in priority order):
  1. bundled first-party packs: ``magi_agent/firstparty/packs/``
  2. user home packs:           ``~/.magi/packs/``
  3. project packs:             ``<cwd>/.magi/packs/``

Mirrors the disk-discovery pattern in ``magi_agent/plugins/native/skills.py``
(rglob a sentinel filename across base dirs; tolerate missing bases).
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from magi_agent.config.flags import flag_str
from magi_agent.packs.manifest import PackManifest, load_manifest_from_toml

_PACK_FILENAME = "pack.toml"


@dataclass(frozen=True)
class DiscoveredPack:
    """A parsed manifest plus where it came from (for relpath resolution)."""

    path: Path          # the pack.toml file
    pack_dir: Path      # directory containing pack.toml (base for spec relpaths)
    manifest: PackManifest


def _bundled_firstparty_base() -> Path:
    # magi_agent/packs/discovery.py -> magi_agent/ -> firstparty/packs
    return Path(__file__).resolve().parent.parent / "firstparty" / "packs"


def default_search_bases() -> list[Path]:
    """Return the ordered search-path bases (bundled first, then user, then cwd)."""
    return [
        _bundled_firstparty_base(),
        Path.home() / ".magi" / "packs",
        Path.cwd() / ".magi" / "packs",
    ]


def discover_pack_files(bases: list[Path]) -> list[DiscoveredPack]:
    """rglob each base for ``pack.toml`` and parse it. Missing bases are skipped.

    Ordering is **base precedence**: bases are walked in the order given (the
    precedence order — bundled first-party, then user, then cwd) and within each
    base the ``pack.toml`` files are ``sorted`` for determinism. The cross-base
    order is therefore preserved, NOT collapsed by a global ``pack_id`` sort: a
    later base wins downstream last-wins registration even when its ``pack_id``
    sorts alphabetically before an earlier base's (the override contract). A
    global re-sort here would let an earlier (lower-precedence) pack load last and
    overwrite the intended override. Duplicate ``pack_id`` across bases is NOT
    resolved here (that is config-aware override territory — handled in Task 1.4).
    """
    discovered: list[DiscoveredPack] = []
    for base in bases:
        if not base.is_dir():
            continue
        for pack_file in sorted(base.rglob(_PACK_FILENAME)):
            manifest = load_manifest_from_toml(pack_file)
            discovered.append(
                DiscoveredPack(
                    path=pack_file,
                    pack_dir=pack_file.parent,
                    manifest=manifest,
                )
            )
    return discovered


_PACKS_CONFIG_MODEL = ConfigDict(frozen=True, populate_by_name=True, extra="ignore")


class PacksConfig(BaseModel):
    """The ``[packs]`` section of ``config.toml`` (D1 override controls).

    ``extra="ignore"`` (not ``forbid``) so unrelated future keys do not crash
    discovery. Lists are coerced to tuples for frozen-ness.
    """

    model_config = _PACKS_CONFIG_MODEL

    disable: tuple[str, ...] = ()
    order: tuple[str, ...] = ()
    override: tuple[str, ...] = ()


def _config_path() -> Path:
    override = flag_str("MAGI_CONFIG")
    if override and override.strip():
        return Path(override).expanduser()
    return Path.home() / ".magi" / "config.toml"


def load_packs_config() -> PacksConfig:
    """Load ``[packs]`` from config.toml. Missing/malformed -> empty config.

    Mirrors ``magi_agent/cli/providers.py``'s tolerant loader: a bad config must
    not crash discovery.
    """
    path = _config_path()
    try:
        with open(path, "rb") as handle:
            raw = tomllib.load(handle)
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return PacksConfig()
    except (OSError, tomllib.TOMLDecodeError):
        return PacksConfig()
    section = raw.get("packs") if isinstance(raw, dict) else None
    if not isinstance(section, dict):
        return PacksConfig()
    try:
        return PacksConfig.model_validate(section)
    except Exception:
        return PacksConfig()


def resolve_enabled_packs(
    discovered: list[DiscoveredPack], config: PacksConfig
) -> list[DiscoveredPack]:
    """Apply enable/disable/order to a discovered set.

    1. drop packs whose ``pack_id`` is in ``config.disable``;
    2. drop packs whose manifest ``default_enabled`` is False (unless re-enabled
       by appearing in ``config.order`` — an explicit order entry is an opt-in);
    3. order: pins in ``config.order`` first (in listed order), then the rest in
       **discovered (base-precedence) order** — the order ``discover_pack_files``
       produced (bundled first-party, then user, then cwd). The rest is NOT
       re-sorted by ``pack_id``: that would collapse base precedence and let a
       lower-precedence pack load last and overwrite the intended override (a
       later base must win downstream last-wins even when its ``pack_id`` sorts
       alphabetically before an earlier base's).

    Override-by-ref collision is resolved downstream in ``catalog_build`` /
    loader (last pack in this returned order wins on a colliding provides ref);
    ``config.override`` is carried for that stage, not consumed here.
    """
    disabled = set(config.disable)
    ordered_ids = list(config.order)
    order_set = set(ordered_ids)

    # Preserve discovered/base order (dict insertion order). On a same-``pack_id``
    # collision across bases the later base's pack replaces the earlier value,
    # which is the intended last-wins-for-same-id behavior.
    by_id: dict[str, DiscoveredPack] = {}
    for disc in discovered:
        by_id[disc.manifest.pack_id] = disc
    kept: dict[str, DiscoveredPack] = {}
    for pack_id, disc in by_id.items():
        if pack_id in disabled:
            continue
        if not disc.manifest.default_enabled and pack_id not in order_set:
            continue
        kept[pack_id] = disc

    pinned = [kept[p] for p in ordered_ids if p in kept]
    rest = [d for pid, d in kept.items() if pid not in order_set]
    return pinned + rest
