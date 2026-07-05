"""Pack discovery (D1): resolve search-path bases and rglob ``pack.toml``.

Search path (in priority order):
  1. bundled first-party packs: ``magi_agent/firstparty/packs/``
  2. user home packs:           ``~/.magi/packs/``
  3. project packs:             ``<cwd>/.magi/packs/``

Mirrors the disk-discovery pattern in ``magi_agent/plugins/native/skills.py``
(rglob a sentinel filename across base dirs; tolerate missing bases).
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from magi_agent.config.flags import flag_str
from magi_agent.packs.manifest import PackManifest, load_manifest_from_toml

_PACK_FILENAME = "pack.toml"


@dataclass(frozen=True)
class DiscoveredPack:
    """A parsed manifest plus where it came from (for relpath resolution)."""

    path: Path  # the pack.toml file
    pack_dir: Path  # directory containing pack.toml (base for spec relpaths)
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
        try:
            is_dir = base.is_dir()
        except OSError:
            continue
        if not is_dir:
            continue
        try:
            pack_files = sorted(base.rglob(_PACK_FILENAME))
        except OSError:
            continue
        for pack_file in pack_files:
            # skip-and-continue mirrors the existing unreadable-base skipping;
            # one broken pack must not poison discovery for every gate that reads
            # static manifests.  OSError covers IsADirectoryError (rglob matches
            # directories named pack.toml) and PermissionError (unreadable files).
            try:
                manifest = load_manifest_from_toml(pack_file)
            except (OSError, ValueError, ValidationError):
                continue
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


def _load_packs_config_file() -> PacksConfig:
    """The static ``[packs]`` section from config.toml. Missing/malformed -> empty.

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


def _packs_state_path() -> Path:
    """Dashboard-written runtime pack install/remove overrides, a JSON sidecar
    next to config.toml. Separate from config.toml so the operator's hand-edited
    file is never rewritten by the dashboard."""
    return _config_path().parent / "packs-state.json"


def load_packs_runtime_state() -> dict[str, bool]:
    """Load the dashboard install/remove overrides: ``{pack_id: enabled}``.

    ``enabled=False`` = the operator removed the pack from the dashboard;
    ``enabled=True`` = installed/kept (and re-enables a ``default_enabled=False``
    pack). Missing/malformed -> ``{}`` (tolerant, like the config loader)."""
    path = _packs_state_path()
    try:
        with open(path, "rb") as handle:
            raw = json.load(handle)
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return {}
    except (OSError, ValueError):
        return {}
    packs = raw.get("packs") if isinstance(raw, dict) else None
    if not isinstance(packs, dict):
        return {}
    return {
        str(pid): bool(val)
        for pid, val in packs.items()
        if isinstance(pid, str) and isinstance(val, bool)
    }


def set_pack_runtime_state(pack_id: str, enabled: bool) -> dict[str, bool]:
    """Persist a single dashboard install/remove decision. Returns the new
    full override map. Read-modify-write of the JSON sidecar."""
    state = load_packs_runtime_state()
    state[pack_id] = enabled
    path = _packs_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"packs": state}, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return state


def load_packs_config() -> PacksConfig:
    """Effective ``[packs]`` config: the config.toml section merged with the
    dashboard install/remove overrides (``packs-state.json``).

    This is the single source of truth every discovery consumer resolves
    through, so a dashboard remove/install takes real effect (not just display):
    ``enabled=False`` adds the id to ``disable`` (dropped in
    :func:`resolve_enabled_packs`); ``enabled=True`` drops it from ``disable``
    and adds it to ``order`` (an explicit opt-in that also re-enables a
    ``default_enabled=False`` pack). The operator's config.toml still wins as
    the base; the override only layers on top.
    """
    base = _load_packs_config_file()
    overrides = load_packs_runtime_state()
    if not overrides:
        return base

    disable = list(base.disable)
    order = list(base.order)
    disable_set = set(disable)
    order_set = set(order)
    for pack_id, want_on in overrides.items():
        if want_on:
            if pack_id in disable_set:
                disable = [d for d in disable if d != pack_id]
                disable_set.discard(pack_id)
            if pack_id not in order_set:
                order.append(pack_id)
                order_set.add(pack_id)
        elif pack_id not in disable_set:
            disable.append(pack_id)
            disable_set.add(pack_id)
    return base.model_copy(update={"disable": tuple(disable), "order": tuple(order)})


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
