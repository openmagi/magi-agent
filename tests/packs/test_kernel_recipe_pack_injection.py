"""Kernel-loaded ``recipe`` provides fold into the recipe-compile registry.

``build_runtime_pack_registry`` reads ``pack.toml`` recipe provides via the
kernel's discovery (genuine ``RecipePackManifest`` specs) and registers them after
the first-party packs. Covers: flag-OFF byte-identical baseline, flag-ON user pack
joins, first-party-wins on collision (no shadow), fail-closed-to-first-party, the
compose-only trust boundary (R1/R4/R6/R7) for untrusted user-dir packs, and the
trusted-exemption for bundled first-party recipe packs.
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


def _write_recipe_pack(
    base: Path, *, pack_id: str, ref: str = "recipe:kerneltest@1", spec_extra: str = ""
) -> None:
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
        "defaultEnabled = false\n" + spec_extra,
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
    monkeypatch.setenv(FLAG, "0")
    assert build_runtime_pack_registry().pack_ids == PackRegistry.with_first_party_packs().pack_ids


def test_flag_off_ignores_a_present_user_pack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(FLAG, "0")
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


# --------------------------------------------------------------------------- #
# Flag ON — compose-only trust boundary for UNTRUSTED (user-dir) packs
# --------------------------------------------------------------------------- #
def test_r1_non_namespaced_pack_dropped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(FLAG, "1")
    _write_recipe_pack(tmp_path, pack_id="myrecipe")  # no ext./publisher namespace
    _patch_bases(monkeypatch, [tmp_path])
    assert build_runtime_pack_registry().pack_ids == PackRegistry.with_first_party_packs().pack_ids


def test_r1_publisher_subnamespace_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(FLAG, "1")
    _write_recipe_pack(tmp_path, pack_id="ext.acme.finance")
    _patch_bases(monkeypatch, [tmp_path])
    assert "ext.acme.finance" in build_runtime_pack_registry().pack_ids


def test_r4_hard_safety_pack_dropped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(FLAG, "1")
    # hardSafety requires optOutAllowed=false to even construct; the compose-only
    # validator must still reject the authority claim.
    _write_recipe_pack(
        tmp_path,
        pack_id="ext.evil",
        spec_extra="hardSafety = true\noptOutAllowed = false\ncustomizable = false\n",
    )
    _patch_bases(monkeypatch, [tmp_path])
    ids = build_runtime_pack_registry().pack_ids
    assert "ext.evil" not in ids
    assert ids == PackRegistry.with_first_party_packs().pack_ids


def test_r6_ownership_pack_dropped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(FLAG, "1")
    _write_recipe_pack(
        tmp_path,
        pack_id="ext.evil",
        spec_extra='adkPrimitiveOwnership = ["ADK Runner owns invocation"]\n',
    )
    _patch_bases(monkeypatch, [tmp_path])
    assert "ext.evil" not in build_runtime_pack_registry().pack_ids


def test_r7_default_enabled_pack_dropped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A defaultEnabled pack would be silently auto-selected globally
    # (compiler promote_as_default); an untrusted pack cannot do that.
    monkeypatch.setenv(FLAG, "1")
    _write_recipe_pack(tmp_path, pack_id="ext.evil", spec_extra="defaultEnabled = true\n")
    _patch_bases(monkeypatch, [tmp_path])
    assert "ext.evil" not in build_runtime_pack_registry().pack_ids


def test_validate_external_recipe_pack_units() -> None:
    from magi_agent.recipes.compiler import RecipePackManifest
    from magi_agent.recipes.kernel_recipe_packs import validate_external_recipe_pack

    def _m(**kw: object) -> RecipePackManifest:
        return RecipePackManifest.model_validate(
            {"packId": "ext.x", "version": "1", "displayName": "x", "description": "x", **kw}
        )

    assert validate_external_recipe_pack(_m()) == ""
    assert validate_external_recipe_pack(_m(packId="plain")) == "r1_namespace_required"
    assert (
        validate_external_recipe_pack(_m(hardSafety=True, optOutAllowed=False, customizable=False))
        == "r4_hard_safety_blocked"
    )
    assert validate_external_recipe_pack(_m(defaultEnabled=True)) == "r7_default_enabled_blocked"
    assert (
        validate_external_recipe_pack(_m(adkPrimitiveOwnership=["owns"]))
        == "r6_ownership_blocked"
    )


# --------------------------------------------------------------------------- #
# Flag ON — a TRUSTED bundled first-party recipe pack bypasses the boundary
# --------------------------------------------------------------------------- #
def test_bundled_first_party_recipe_pack_is_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    # The bundled recipe_authoring_static pack uses the openmagi.* (first-party)
    # namespace, which R1 would reject for an untrusted pack. Discovered from the
    # bundled base it is trusted and joins as-is. (It ships defaultEnabled=false so
    # turning the kernel flag on does not auto-select it globally; see the spec and
    # test_flag_on_introduces_no_new_default_enabled_recipe below.)
    from magi_agent.packs.discovery import _bundled_firstparty_base

    monkeypatch.setenv(FLAG, "1")
    _patch_bases(monkeypatch, [_bundled_firstparty_base()])
    assert "openmagi.authoring-static" in build_runtime_pack_registry().pack_ids


def test_flag_on_introduces_no_new_default_enabled_recipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Turning the kernel recipe flag ON must not add a globally-auto-selected
    (``defaultEnabled``) recipe that the first-party baseline does not already
    have. A bundled pack that is ``defaultEnabled`` but absent from
    ``_first_party_packs()`` would be promoted globally (``promote_as_default``)
    and hijack every turn, dropping coding/chat verification wiring. This guards
    that invariant against any future bundled recipe pack (regression: the
    bundled ``authoring-static`` pack once shipped ``defaultEnabled=true``).
    """
    monkeypatch.setenv(FLAG, "1")

    baseline = PackRegistry.with_first_party_packs()
    on_registry = build_runtime_pack_registry()

    baseline_defaults = {
        pid for pid in baseline.pack_ids if baseline.get(pid).default_enabled
    }
    on_defaults = {
        pid for pid in on_registry.pack_ids if on_registry.get(pid).default_enabled
    }
    assert on_defaults == baseline_defaults
