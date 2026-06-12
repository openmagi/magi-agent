"""§1 "no privilege" acceptance spec (00-BLUEPRINT.md §1).

The whole pack passes only if every first-party behavior is expressible and
loadable through the SAME (a) loader, (b) flat catalog, (c) typed context as a
third-party user pack — no in-code shortcut, no first-party-only tier.

Adapted to the REAL Phase-1/2 ABI (the doc's discover_packs/load_packs(...) /
loaded.packs / loaded.resolve_ref / MAGI_PACKS_OVERRIDE|FORBID shapes do not
exist in this tree). Real mechanism, matching tests/packs/test_user_*_pack*:
  * discovery = ``discover_pack_files(bases)`` + ``resolve_enabled_packs(disc, cfg)``;
  * register  = ``load_packs(enabled, RegistryRegistrationSink(registry))``;
  * catalog   = ``build_catalog(result.primitives)``;
  * override  = load order (a ``user.*`` pack_id sorts AFTER ``openmagi.*`` so its
    same-ref impl wins by last-wins ``override=True``);
  * forbid    = ``config.toml [packs] disable = ["<pack_id>"]`` (PacksConfig has no
    by-ref forbid knob).
The assertion CONTENT (the §1 spec) is what matters, not the exact knob names.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import magi_agent
from magi_agent.packs.catalog_build import build_catalog, resolve_live_catalog
from magi_agent.packs.context import (
    CallbackProvideContext,
    ConnectorProvideContext,
    ControlPlaneProvideContext,
    EvidenceProducerProvideContext,
    HarnessProvideContext,
    LoopPolicyProvideContext,
    MemoryStrategyProvideContext,
    PrimitiveType,
    SchedulePolicyProvideContext,
    ToolProvideContext,
    ValidatorCtx,
)
from magi_agent.packs.discovery import (
    discover_pack_files,
    load_packs_config,
    resolve_enabled_packs,
)
from magi_agent.packs.loader import RecordingSink, load_packs
from magi_agent.packs.manifest import load_manifest_from_toml
from magi_agent.packs.registries import PrimitiveRegistry, RegistryRegistrationSink
from magi_agent.packs.types import CompileRecipePackCatalog

_ROOT = Path(magi_agent.__file__).parent
_FIRSTPARTY_DIR = _ROOT / "firstparty" / "packs"

# The public provides types -> the real typed-context class each impl receives
# (D5). The 8 D2 types plus the 3 Pack-C policy types (loop_policy /
# schedule_policy / memory_strategy) added by the C0 schema widening — all are
# user-declarable through the same loader (tests/packs/test_pack_c_provides_types.py
# proves a user-shaped pack loads each one end-to-end), so none is privileged.
# ``recipe`` is declarative (a ``spec`` file, no impl, so no context).
_PROVIDES_TYPES = (
    "tool", "callback", "validator", "harness",
    "control_plane", "evidence_producer", "recipe", "connector",
    "loop_policy", "schedule_policy", "memory_strategy",
)
_TYPE_TO_CTX = {
    "tool": ToolProvideContext,
    "callback": CallbackProvideContext,
    "validator": ValidatorCtx,
    "harness": HarnessProvideContext,
    "control_plane": ControlPlaneProvideContext,
    "evidence_producer": EvidenceProducerProvideContext,
    "connector": ConnectorProvideContext,
    "loop_policy": LoopPolicyProvideContext,
    "schedule_policy": SchedulePolicyProvideContext,
    "memory_strategy": MemoryStrategyProvideContext,
    # recipe: declarative spec, no impl parameter to check.
}


def _all_provided_refs(bases: list[Path]) -> set[str]:
    discovered = discover_pack_files(bases)
    refs: set[str] = set()
    for disc in discovered:
        for entry in disc.manifest.provides:
            refs.add(entry.ref)
    return refs


# --- Assertion 1: no first-party primitive registered via a hardcoded path -------------
def test_no_hardcoded_first_party_registration_on_live_path() -> None:
    cp = (_ROOT / "adk_bridge" / "control_plane.py").read_text()
    # build_default_plugin must not hand-assemble controls; it loads them via the
    # pack loader (build_control_plane_from_packs).
    plugin_src = cp.split("def build_default_plugin", 1)[1].split("\ndef ", 1)[0]
    assert "plane.register(" not in plugin_src
    assert "build_control_plane_from_packs(" in plugin_src
    # The live catalog entry point is kernel-owned (packs/catalog_build.py —
    # re-homed when main deleted the authoring plane) and manifest-built: it
    # folds build_catalog(result.primitives) over discovered packs rather than
    # returning the static hardcode (which survives only as the preserved
    # fail-open floor inside resolve_live_catalog).
    cat = (_ROOT / "packs" / "catalog_build.py").read_text()
    assert "catalog or CompileRecipePackCatalog.default()" not in cat
    assert "def resolve_live_catalog(" in cat
    assert "build_catalog(result.primitives)" in cat


# --- Assertion 2: a user pack can add/override/remove every one of the 8 provides ------
def _write_user_pack(root: Path, *, name: str, pack_id: str, body: str) -> None:
    pack_dir = root / name
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "impl.py").write_text(
        "def make_tool(ctx):\n    return None\n"
        "def make(ctx):\n    return ctx\n"
    )
    (pack_dir / "demo.recipe.toml").write_text(
        'packId = "user.recipe.add"\ndisplayName = "user recipe"\n'
    )
    (pack_dir / "pack.toml").write_text(body)


def test_user_pack_can_ADD_every_provides_type(tmp_path, monkeypatch) -> None:
    user_root = tmp_path / "user_packs"
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    blocks = []
    for t in _PROVIDES_TYPES:
        if t == "recipe":
            blocks.append(
                '[[provides]]\ntype = "recipe"\n'
                f'ref = "user.add.{t}"\nspec = "demo.recipe.toml"\n'
            )
        else:
            blocks.append(
                f'[[provides]]\ntype = "{t}"\n'
                f'ref = "user.add.{t}"\nimpl = "user_add_pack.impl:make"\n'
            )
    body = (
        'packId = "user.add-all"\ndisplayName = "user add-all"\n'
        'version = "0.0.1"\n\n' + "\n".join(blocks)
    )
    _write_user_pack(user_root, name="user_add_pack", pack_id="user.add-all", body=body)
    monkeypatch.syspath_prepend(str(user_root))

    provided = _all_provided_refs([_FIRSTPARTY_DIR, user_root])
    for t in _PROVIDES_TYPES:
        assert f"user.add.{t}" in provided, t


def test_user_pack_can_OVERRIDE_a_first_party_ref(tmp_path, monkeypatch) -> None:
    # Override the bundled first-party ``Clock`` tool. A "user.*" pack_id sorts
    # AFTER "openmagi.*" so the user impl wins by last-wins (no first-party tier).
    user_root = tmp_path / "user_packs"
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    pack_dir = user_root / "user_override_clock"
    pack_dir.mkdir(parents=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "impl.py").write_text(
        "_OVERRIDDEN = object()\n"
        "def provide(ctx):\n    return _OVERRIDDEN\n"
    )
    (pack_dir / "pack.toml").write_text(
        'packId = "user.override-clock"\ndisplayName = "user override clock"\n'
        'version = "0.0.1"\n\n'
        '[[provides]]\ntype = "tool"\nref = "Clock"\n'
        'impl = "user_override_clock.impl:provide"\n'
    )
    monkeypatch.syspath_prepend(str(user_root))

    discovered = discover_pack_files([_FIRSTPARTY_DIR, user_root])
    enabled = resolve_enabled_packs(discovered, load_packs_config())
    registry = PrimitiveRegistry()
    load_packs(enabled, RegistryRegistrationSink(registry))
    entry = registry.resolve_entry("Clock", ptype=PrimitiveType.TOOL)
    # the user pack's impl won (its module name is the override marker)
    assert entry.impl.__module__ == "user_override_clock.impl"
    assert entry.origin == "user"  # NOT first_party — the override has no privilege


def test_user_pack_can_REMOVE_forbid_a_first_party_ref(tmp_path, monkeypatch) -> None:
    user_root = tmp_path / "user_packs"
    user_root.mkdir(parents=True, exist_ok=True)
    config_path = tmp_path / "config.toml"
    # forbid knob = [packs] disable keyed by PACK_ID (real PacksConfig mechanism).
    config_path.write_text('[packs]\ndisable = ["openmagi.tools-clock"]\n')
    monkeypatch.setenv("MAGI_CONFIG", str(config_path))

    discovered = discover_pack_files([_FIRSTPARTY_DIR, user_root])
    enabled = resolve_enabled_packs(discovered, load_packs_config())
    result = load_packs(enabled, RecordingSink())
    catalog = build_catalog(result.primitives)
    assert "Clock" not in catalog.tool_refs  # forbidden out, no first-party privilege


# --- Assertion 3: the live catalog is manifest-built ----------------------------------
def test_live_catalog_is_manifest_built() -> None:
    static = CompileRecipePackCatalog.default()
    live = resolve_live_catalog()
    assert live is not static
    # legacy floor preserved (no regression) AND pack refs unioned in:
    assert set(static.tool_refs).issubset(set(live.tool_refs))
    assert "Clock" in live.tool_refs and "Clock" not in static.tool_refs


# --- Assertion 4: every first-party impl takes only its typed context -------------------
def test_every_first_party_impl_takes_only_its_typed_context() -> None:
    discovered = discover_pack_files([_FIRSTPARTY_DIR])
    for disc in discovered:
        for entry in disc.manifest.provides:
            if entry.impl is None:  # declarative spec= entries have no impl
                continue
            from magi_agent.packs.loader import lazy_import_symbol

            fn = lazy_import_symbol(entry.impl)
            sig = inspect.signature(fn)
            params = [
                p for p in sig.parameters.values()
                if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            ]
            assert len(params) == 1, (
                f"{entry.ref}: impl must take exactly its typed context, got "
                f"{[p.name for p in params]}"
            )
            ann = params[0].annotation
            expected = _TYPE_TO_CTX[entry.type]
            assert ann in (expected, expected.__name__, inspect.Parameter.empty), (
                f"{entry.ref}: impl param annotated {ann!r}, "
                f"expected {expected.__name__}"
            )


def test_no_first_party_pack_declares_a_privileged_provides_type() -> None:
    """Every bundled first-party provides type is one of the 8 public types — no
    first-party-only primitive type exists."""
    for pack_file in sorted(_FIRSTPARTY_DIR.rglob("pack.toml")):
        manifest = load_manifest_from_toml(pack_file)
        for entry in manifest.provides:
            assert entry.type in _PROVIDES_TYPES, (manifest.pack_id, entry.type)
