from magi_agent.authoring.compiler import CompileRecipePackCatalog
from magi_agent.packs.catalog_build import build_catalog
from magi_agent.packs.loader import LoadedPrimitive


def _prim(type_: str, ref: str, pack_id: str = "p") -> LoadedPrimitive:
    return LoadedPrimitive(type=type_, ref=ref, pack_id=pack_id, impl=object())


def test_build_catalog_returns_compile_recipe_pack_catalog():
    catalog = build_catalog([_prim("tool", "FileWrite")])
    assert isinstance(catalog, CompileRecipePackCatalog)
    assert catalog.tool_refs == ("FileWrite",)


def test_build_catalog_maps_each_type_to_its_field():
    prims = [
        _prim("tool", "T"),
        _prim("connector", "C"),
        _prim("validator", "V"),
        _prim("harness", "H"),
        _prim("evidence_producer", "E"),
        _prim("control_plane", "CP"),
        _prim("callback", "CB"),
    ]
    catalog = build_catalog(prims)
    assert catalog.tool_refs == ("T",)
    assert catalog.connector_refs == ("C",)
    assert catalog.validator_refs == ("V",)
    assert catalog.harness_refs == ("H",)
    assert catalog.evidence_producer_refs == ("E",)
    # control_plane + callback both land in pluginRefs, order-preserved
    assert catalog.plugin_refs == ("CP", "CB")


def test_build_catalog_has_empty_hard_invariant_tiers_for_oss_local():
    catalog = build_catalog([_prim("tool", "T")])
    assert catalog.hard_invariant_refs == ()
    assert catalog.required_hard_invariant_refs == ()


def test_build_catalog_recipe_entries_are_not_catalog_refs():
    catalog = build_catalog(
        [
            LoadedPrimitive(type="recipe", ref="r@1", pack_id="p", spec_path=None),
            _prim("tool", "T"),
        ]
    )
    assert catalog.tool_refs == ("T",)
    # recipe ref does not appear in any *_refs tuple
    dumped = catalog.model_dump()
    assert not any("r@1" in tuple(v) for v in dumped.values() if isinstance(v, (list, tuple)))


def test_build_catalog_last_wins_dedup_on_colliding_ref():
    # two tools, same ref -> single entry (catalog refs are a set-union, last wins position)
    catalog = build_catalog([_prim("tool", "Dup", "p.a"), _prim("tool", "Dup", "p.b")])
    assert catalog.tool_refs == ("Dup",)


def test_build_catalog_empty_is_valid():
    catalog = build_catalog([])
    assert isinstance(catalog, CompileRecipePackCatalog)
    assert catalog.tool_refs == ()
