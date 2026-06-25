"""PR4 — recipe-as-code: a recipe pack may compute its RecipePackManifest with
THEIR OWN code (``spec_callable = "module.path:provide_recipe"``) instead of a
declarative ``spec`` TOML, registered with the SAME external trust validation as
declarative recipes.

Default-OFF gating contract (see the module + flag docstrings):

* The manifest schema ACCEPTS a ``spec_callable`` shape always (so a malformed
  ref still errors at parse), but ACTIVATION is gated in the loader: when
  ``MAGI_RECIPE_AS_CODE_ENABLED`` is OFF, a ``spec_callable`` entry is dropped at
  load time and the callable is NEVER imported. Discovery is therefore byte-
  identical to before the feature existed (no LoadedPrimitive, no registry entry).
* When ON, the callable is imported lazily, invoked ONCE at registration, the
  returned ``RecipePackManifest`` (or dict) is validated through the SAME
  ``validate_external_recipe_pack`` boundary used for declarative recipes, and
  registered into ``registries.recipes``. Fail-closed: a callable that raises,
  returns the wrong type, or fails validation drops the pack with a warning and
  never crashes the run.
"""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.registries import load_into_registries

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"


def _write_code_recipe_pack(
    root: Path,
    name: str,
    pack_id: str,
    ref: str,
    impl_body: str,
) -> Path:
    """Write a user pack whose recipe entry carries ``spec_callable`` + an impl."""
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
    (pack_dir / "impl.py").write_text(impl_body)
    return pack_dir


# A callable that returns a valid ext.* non-hard-safety manifest dict.
_VALID_EXT_IMPL = '''\
"""User code recipe — computes its RecipePackManifest at registration time."""
from __future__ import annotations


def provide_recipe():
    return {
        "packId": "ext.acme.flow",
        "version": "1",
        "displayName": "Acme Flow",
        # FileWrite is a canonical known-ref in the catalog default, so this
        # recipe closes R2 ref-closure (PR5).
        "description": "A code-computed user recipe.",
        "toolRefs": ["FileWrite"],
    }
'''

# A callable returning a hard-safety manifest — must be dropped by R4.
_HARD_SAFETY_IMPL = '''\
"""User code recipe that illegitimately asserts hard safety."""
from __future__ import annotations


def provide_recipe():
    return {
        "packId": "ext.acme.unsafe",
        "version": "1",
        "displayName": "Acme Unsafe",
        "description": "Tries to claim hard safety.",
        "hardSafety": True,
        "optOutAllowed": False,
        "customizable": False,
    }
'''

# A callable that raises — must be dropped, never crash the run.
_RAISING_IMPL = '''\
"""User code recipe whose callable raises."""
from __future__ import annotations


def provide_recipe():
    raise RuntimeError("boom from publisher code")
'''


def test_flag_off_code_recipe_ignored_and_never_imported(
    tmp_path: Path, monkeypatch
) -> None:
    """Flag OFF: a ``spec_callable`` entry is dropped at load time and the
    callable module is never imported (discovery byte-identical)."""
    import sys

    user_root = tmp_path / "user_packs"
    _write_code_recipe_pack(
        user_root, "code_recipe_off", "ext.acme.off", "recipe:off-flow@1",
        # Top-level side effect would prove an import happened — we instead
        # assert the module never lands in sys.modules.
        _VALID_EXT_IMPL.replace("ext.acme.flow", "ext.acme.off"),
    )
    monkeypatch.syspath_prepend(str(user_root))
    monkeypatch.delenv("MAGI_RECIPE_AS_CODE_ENABLED", raising=False)

    registries, report = load_into_registries([_FIRST_PARTY_ROOT, user_root])

    assert registries.recipes.resolve("recipe:off-flow@1") is None
    assert "recipe:off-flow@1" not in report.registered
    # Byte-identical discovery: the publisher's impl module was never imported.
    assert "code_recipe_off.impl" not in sys.modules


def test_flag_on_valid_ext_code_recipe_registered(
    tmp_path: Path, monkeypatch
) -> None:
    """Flag ON + a valid ext.* non-hard-safety manifest => registered and
    selectable in ``registries.recipes``."""
    user_root = tmp_path / "user_packs"
    _write_code_recipe_pack(
        user_root, "code_recipe_ok", "ext.acme.flow", "recipe:code-flow@1",
        _VALID_EXT_IMPL,
    )
    monkeypatch.syspath_prepend(str(user_root))
    monkeypatch.setenv("MAGI_RECIPE_AS_CODE_ENABLED", "1")

    registries, report = load_into_registries([_FIRST_PARTY_ROOT, user_root])

    manifest = registries.recipes.resolve("recipe:code-flow@1")
    assert manifest is not None
    assert manifest.pack_id == "ext.acme.flow"
    assert "recipe:code-flow@1" in report.registered


def test_flag_on_hard_safety_code_recipe_dropped(
    tmp_path: Path, monkeypatch
) -> None:
    """Flag ON + a hard-safety (or non-ext) manifest => dropped by trust
    validation (``validate_external_recipe_pack``)."""
    user_root = tmp_path / "user_packs"
    _write_code_recipe_pack(
        user_root, "code_recipe_unsafe", "ext.acme.unsafe", "recipe:unsafe-flow@1",
        _HARD_SAFETY_IMPL,
    )
    monkeypatch.syspath_prepend(str(user_root))
    monkeypatch.setenv("MAGI_RECIPE_AS_CODE_ENABLED", "1")

    registries, report = load_into_registries([_FIRST_PARTY_ROOT, user_root])

    assert registries.recipes.resolve("recipe:unsafe-flow@1") is None
    assert "recipe:unsafe-flow@1" not in report.registered


def test_flag_on_raising_code_recipe_dropped_no_crash(
    tmp_path: Path, monkeypatch
) -> None:
    """Flag ON + a callable that raises => dropped, run never crashes."""
    user_root = tmp_path / "user_packs"
    _write_code_recipe_pack(
        user_root, "code_recipe_boom", "ext.acme.boom", "recipe:boom-flow@1",
        _RAISING_IMPL,
    )
    monkeypatch.syspath_prepend(str(user_root))
    monkeypatch.setenv("MAGI_RECIPE_AS_CODE_ENABLED", "1")

    # Must not raise.
    registries, report = load_into_registries([_FIRST_PARTY_ROOT, user_root])

    assert registries.recipes.resolve("recipe:boom-flow@1") is None
    assert "recipe:boom-flow@1" not in report.registered
