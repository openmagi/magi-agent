"""PR1 — kernel-loaded ``recipe`` provides fold into the recipe-compile registry.

``build_runtime_pack_registry`` reads the kernel's ``registries.recipes`` (genuine
``RecipePackManifest`` objects materialised from ``pack.toml`` recipe provides) and
registers them after the first-party packs. Covers: flag-OFF byte-identical
baseline, flag-ON user pack joins, first-party-wins on collision (no shadow), and
fail-closed-to-first-party on discovery error.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.recipes.compiler import PackRegistry
from magi_agent.recipes.kernel_recipe_packs import (
    MAGI_KERNEL_RECIPE_PACKS_ENABLED_ENV as FLAG,
)
from magi_agent.recipes.kernel_recipe_packs import (
    build_runtime_pack_registry,
)

_A_FIRST_PARTY_PACK_ID = "openmagi.context-safety"


def _write_recipe_pack(base: Path, *, pack_id: str, ref: str = "recipe:kerneltest@1") -> None:
    pack_dir = base / "user_recipe_pack"
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "pack.toml").write_text(
        "packId = \"pack.user-recipe\"\n"
        "displayName = \"User recipe pack\"\n"
        "version = \"1.0.0\"\n"
        "description = \"A user-authored recipe pack for testing.\"\n\n"
        "[[provides]]\n"
        'type = "recipe"\n'
        f'ref = "{ref}"\n'
        'spec = "user.recipe.toml"\n',
        encoding="utf-8",
    )
    (pack_dir / "user.recipe.toml").write_text(
        f'packId = "{pack_id}"\n'
        'version = "1"\n'
        'displayName = "User recipe"\n'
        'description = "A user-authored declarative recipe."\n'
        "defaultEnabled = false\n",
        encoding="utf-8",
    )


def _patch_bases(monkeypatch: pytest.MonkeyPatch, bases: list[Path]) -> None:
    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases", lambda: list(bases)
    )


# --------------------------------------------------------------------------- #
# Flag OFF — byte-identical baseline
# --------------------------------------------------------------------------- #
def test_flag_off_is_byte_identical_to_first_party(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    assert build_runtime_pack_registry().pack_ids == PackRegistry.with_first_party_packs().pack_ids


def test_flag_off_ignores_a_present_user_pack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    _write_recipe_pack(tmp_path, pack_id="ext.kerneltest")
    _patch_bases(monkeypatch, [tmp_path])
    assert build_runtime_pack_registry().pack_ids == PackRegistry.with_first_party_packs().pack_ids


# --------------------------------------------------------------------------- #
# Flag ON — user recipe pack joins
# --------------------------------------------------------------------------- #
def test_flag_on_user_recipe_pack_joins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(FLAG, "1")
    _write_recipe_pack(tmp_path, pack_id="ext.kerneltest")
    _patch_bases(monkeypatch, [tmp_path])
    ids = build_runtime_pack_registry().pack_ids
    assert "ext.kerneltest" in ids
    # First-party packs are all still present (additive, first-party preserved).
    for fp in PackRegistry.with_first_party_packs().pack_ids:
        assert fp in ids


# --------------------------------------------------------------------------- #
# Flag ON — first-party-wins (a kernel pack cannot shadow first-party)
# --------------------------------------------------------------------------- #
def test_flag_on_first_party_collision_is_dropped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(FLAG, "1")
    _write_recipe_pack(tmp_path, pack_id=_A_FIRST_PARTY_PACK_ID)  # shadow attempt
    _patch_bases(monkeypatch, [tmp_path])
    first_party = PackRegistry.with_first_party_packs()
    registry = build_runtime_pack_registry()
    # The colliding kernel pack is dropped: the id set is unchanged and the
    # retained manifest is the FIRST-PARTY one (the user's is its description).
    assert registry.pack_ids == first_party.pack_ids
    assert registry.get(_A_FIRST_PARTY_PACK_ID).description == first_party.get(
        _A_FIRST_PARTY_PACK_ID
    ).description


# --------------------------------------------------------------------------- #
# Flag ON — fail-closed-to-first-party
# --------------------------------------------------------------------------- #
def test_flag_on_discovery_error_falls_closed_to_first_party(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FLAG, "1")

    def _boom() -> list[Path]:
        raise RuntimeError("discovery exploded")

    monkeypatch.setattr("magi_agent.packs.discovery.default_search_bases", _boom)
    assert build_runtime_pack_registry().pack_ids == PackRegistry.with_first_party_packs().pack_ids
