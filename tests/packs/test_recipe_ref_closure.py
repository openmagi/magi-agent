"""R2 ref-closure for recipe packs (PR5).

An external (``ext.*``) recipe pack may declare refs (tool/validator/evidence/
instruction/approval-gate/callback/checkpoint/audit/granted-tool) that no pack in
the loaded registry actually provides. With recipe-as-code (PR4) a third party can
emit such a recipe; a dangling ref is unsafe. R2 closes an external pack's refs
against the declared-ref universe of the admitted recipe-pack set (the union, per
family, of every ref declared by the trusted/first-party packs plus the refs a
publisher self-declares in the same bundle). A dangling external ref drops the
pack fail-closed; a fully-closed external pack is kept. First-party packs are
trusted and never dropped by closure (they DEFINE the universe).

Applies uniformly to declarative TOML recipe specs and PR4 code recipes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import magi_agent
from magi_agent.packs.registries import load_into_registries
from magi_agent.recipes.compiler import PackRegistry, RecipePackManifest
from magi_agent.recipes.kernel_recipe_packs import (
    MAGI_KERNEL_RECIPE_PACKS_ENABLED_ENV as FLAG,
)
from magi_agent.recipes.kernel_recipe_packs import (
    build_recipe_ref_universe,
    build_runtime_pack_registry,
    recipe_pack_ref_closure_reason,
)

# A first-party ref known to be in the universe (a tool ref the research pack
# declares); a closing external pack may reuse it.
_KNOWN_FIRST_PARTY_TOOL_REF = "tool:file.read"
# A canonical catalog known-ref (CompileRecipePackCatalog.default toolRefs).
_KNOWN_CATALOG_TOOL_REF = "FileWrite"

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"


def _write_code_recipe_pack(
    root: Path, name: str, pack_id: str, ref: str, tool_refs: str
) -> None:
    """Write a user pack whose recipe entry computes its manifest via code (PR4)."""
    pack_dir = root / name
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "pack.toml").write_text(
        f"packId = {pack_id!r}\ndisplayName = {pack_id!r}\nversion = \"0.0.1\"\n\n"
        "[[provides]]\n"
        'type = "recipe"\n'
        f"ref = {ref!r}\n"
        f'spec_callable = "{name}.impl:provide_recipe"\n'
    )
    (pack_dir / "impl.py").write_text(
        "from __future__ import annotations\n\n\n"
        "def provide_recipe():\n"
        "    return {\n"
        f'        "packId": "{pack_id}",\n'
        '        "version": "1",\n'
        '        "displayName": "x",\n'
        '        "description": "x",\n'
        f"        \"toolRefs\": {tool_refs},\n"
        "    }\n"
    )


def _write_recipe_pack(
    base: Path, *, pack_id: str, ref: str = "recipe:reftest@1", spec_extra: str = ""
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
# Closure helper units (the universe + the per-pack closure verdict)
# --------------------------------------------------------------------------- #
def test_first_party_packs_all_close_against_their_universe() -> None:
    # First-party safety: every shipped first-party recipe pack must close
    # against the first-party universe (else the universe mapping is wrong).
    registry = PackRegistry.with_first_party_packs()
    universe = build_recipe_ref_universe(registry.values())
    for pack in registry.values():
        assert recipe_pack_ref_closure_reason(pack, universe) == "", pack.pack_id


def test_dangling_external_ref_is_reported() -> None:
    registry = PackRegistry.with_first_party_packs()
    universe = build_recipe_ref_universe(registry.values())
    dangling = RecipePackManifest.model_validate(
        {
            "packId": "ext.acme.dangling",
            "version": "1",
            "displayName": "x",
            "description": "x",
            "validatorRefs": ["validator:ext.acme.totally-made-up@1"],
        }
    )
    assert recipe_pack_ref_closure_reason(dangling, universe) == "r2_unresolved_ref"


def test_external_pack_does_not_self_bless_its_own_dangling_ref() -> None:
    # A recipe manifest cannot distinguish "a ref I provide" from "a ref I merely
    # reference": every field is a reference. An external pack therefore cannot put
    # its own dangling ref into the trusted universe; it must compose over refs that
    # already exist in the trusted runtime.
    registry = PackRegistry.with_first_party_packs()
    trusted_universe = build_recipe_ref_universe(registry.values())
    pack = RecipePackManifest.model_validate(
        {
            "packId": "ext.acme.flow",
            "version": "1",
            "displayName": "x",
            "description": "x",
            "validatorRefs": ["validator:ext.acme.local@1"],
        }
    )
    assert recipe_pack_ref_closure_reason(pack, trusted_universe) == "r2_unresolved_ref"


def test_external_pack_closing_over_trusted_refs_closes() -> None:
    # Composing over a ref that genuinely exists in the trusted runtime closes.
    registry = PackRegistry.with_first_party_packs()
    trusted_universe = build_recipe_ref_universe(registry.values())
    pack = RecipePackManifest.model_validate(
        {
            "packId": "ext.acme.flow",
            "version": "1",
            "displayName": "x",
            "description": "x",
            "toolRefs": [_KNOWN_FIRST_PARTY_TOOL_REF],
        }
    )
    assert recipe_pack_ref_closure_reason(pack, trusted_universe) == ""


# --------------------------------------------------------------------------- #
# Flag ON — declarative external recipe pack with a dangling ref is dropped
# --------------------------------------------------------------------------- #
def test_external_pack_with_dangling_ref_is_dropped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(FLAG, "1")
    _write_recipe_pack(
        tmp_path,
        pack_id="ext.acme.dangling",
        spec_extra='validatorRefs = ["validator:ext.acme.nope@1"]\n',
    )
    _patch_bases(monkeypatch, [tmp_path])
    registry = build_runtime_pack_registry()
    assert "ext.acme.dangling" not in registry.pack_ids
    assert registry.pack_ids == PackRegistry.with_first_party_packs().pack_ids


def test_external_pack_with_closing_refs_is_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(FLAG, "1")
    _write_recipe_pack(
        tmp_path,
        pack_id="ext.acme.flow",
        spec_extra=f'toolRefs = ["{_KNOWN_FIRST_PARTY_TOOL_REF}"]\n',
    )
    _patch_bases(monkeypatch, [tmp_path])
    assert "ext.acme.flow" in build_runtime_pack_registry().pack_ids


def test_external_pack_cannot_self_bless_dangling_ref_via_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The pack references a validator ref nobody trusted declares: it cannot put
    # the ref into the universe itself, so it is dropped.
    monkeypatch.setenv(FLAG, "1")
    _write_recipe_pack(
        tmp_path,
        pack_id="ext.acme.self",
        spec_extra='validatorRefs = ["validator:ext.acme.self-local@1"]\n',
    )
    _patch_bases(monkeypatch, [tmp_path])
    assert "ext.acme.self" not in build_runtime_pack_registry().pack_ids


# --------------------------------------------------------------------------- #
# Flag ON — closure must not drop any first-party pack
# --------------------------------------------------------------------------- #
def test_first_party_packs_all_retained_under_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FLAG, "1")
    on_ids = set(build_runtime_pack_registry().pack_ids)
    for fp in PackRegistry.with_first_party_packs().pack_ids:
        assert fp in on_ids


# --------------------------------------------------------------------------- #
# PR4 code recipes: R2 applies uniformly to the spec_callable path
# --------------------------------------------------------------------------- #
def test_code_recipe_with_dangling_ref_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_root = tmp_path / "user_packs"
    _write_code_recipe_pack(
        user_root, "code_dangling", "ext.acme.codedangling", "recipe:code-dangling@1",
        '["tool:ext.acme.made-up"]',
    )
    monkeypatch.syspath_prepend(str(user_root))
    monkeypatch.setenv("MAGI_RECIPE_AS_CODE_ENABLED", "1")
    registries, report = load_into_registries([_FIRST_PARTY_ROOT, user_root])
    assert registries.recipes.resolve("recipe:code-dangling@1") is None
    assert "recipe:code-dangling@1" not in report.registered


def test_code_recipe_with_closing_ref_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_root = tmp_path / "user_packs"
    _write_code_recipe_pack(
        user_root, "code_ok", "ext.acme.codeok", "recipe:code-ok@1",
        f'["{_KNOWN_CATALOG_TOOL_REF}"]',
    )
    monkeypatch.syspath_prepend(str(user_root))
    monkeypatch.setenv("MAGI_RECIPE_AS_CODE_ENABLED", "1")
    registries, report = load_into_registries([_FIRST_PARTY_ROOT, user_root])
    assert registries.recipes.resolve("recipe:code-ok@1") is not None
    assert "recipe:code-ok@1" in report.registered
