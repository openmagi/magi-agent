"""Group C.2 — a USER recipe pack can ADD / OVERRIDE / REMOVE recipe refs with no
first-party privilege (§1). Recipes are declarative ``spec`` entries; override =
last-wins load order, remove = ``[packs] disable`` by pack_id."""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.registries import load_into_registries

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"


def _write_pack(root: Path, name: str, pack_id: str, provides: str, specs: dict[str, str]) -> None:
    pack_dir = root / name
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "pack.toml").write_text(
        f"packId = {pack_id!r}\ndisplayName = {pack_id!r}\nversion = \"0.0.1\"\n\n" + provides
    )
    for fname, body in specs.items():
        (pack_dir / fname).write_text(body)


def test_user_recipe_add_override_remove(tmp_path: Path, monkeypatch) -> None:
    user_root = tmp_path / "user_packs"
    _write_pack(
        user_root, "user_recipe", "user.recipe",
        "[[provides]]\ntype = \"recipe\"\nref = \"recipe:my-flow@1\"\nspec = \"my_flow.recipe.toml\"\n\n"
        "[[provides]]\ntype = \"recipe\"\nref = \"recipe:authoring-static@1\"\nspec = \"override.recipe.toml\"\n",
        {
            "my_flow.recipe.toml": (
                'packId = "user.my-flow"\nversion = "1"\ndisplayName = "My Flow"\n'
                'description = "A user flow."\ntoolRefs = ["FileRead"]\n'
            ),
            "override.recipe.toml": (
                'packId = "openmagi.authoring-static"\nversion = "2"\n'
                'displayName = "Authoring OVERRIDE"\n'
                'description = "Overridden authoring recipe."\n'
                'validatorRefs = ["verifier:sourceOpened@1"]\n'
            ),
        },
    )
    _write_pack(
        user_root, "user_recipe_rm", "user.recipe-rm",
        "[[provides]]\ntype = \"recipe\"\nref = \"recipe:removable@1\"\nspec = \"rm.recipe.toml\"\n",
        {
            "rm.recipe.toml": (
                'packId = "user.removable"\nversion = "1"\ndisplayName = "rm"\n'
                'description = "removable"\n'
            ),
        },
    )
    monkeypatch.syspath_prepend(str(user_root))
    config_path = tmp_path / "config.toml"
    config_path.write_text('[packs]\ndisable = ["user.recipe-rm"]\n')
    monkeypatch.setenv("MAGI_CONFIG", str(config_path))

    registries, _ = load_into_registries([_FIRST_PARTY_ROOT, user_root])

    assert registries.recipes.resolve("recipe:my-flow@1") is not None  # ADD
    assert registries.recipes.resolve("recipe:authoring-static@1").version == "2"  # OVERRIDE
    assert registries.recipes.resolve("recipe:removable@1") is None  # REMOVE
