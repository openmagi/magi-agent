from pathlib import Path

from magi_agent.authoring.compiler import CompileRecipePackCatalog
from magi_agent.packs.loader import RecordingSink, load_from_bases

_FIXTURE_BASE = Path(__file__).parent / "fixtures"


def test_pipeline_discovers_loads_and_builds_catalog():
    sink = RecordingSink()
    result, catalog = load_from_bases([_FIXTURE_BASE], sink)

    refs = {(p.type, p.ref) for p in result.primitives}
    assert ("tool", "DemoTool") in refs
    assert ("validator", "validator:demo@1") in refs
    assert ("control_plane", "cp.demo@1") in refs
    assert ("recipe", "recipe.demo@1") in refs

    # code impls were lazily imported; recipe carries a resolved spec path.
    by_ref = {p.ref: p for p in result.primitives}
    assert callable(by_ref["DemoTool"].impl)
    assert by_ref["recipe.demo@1"].impl is None
    assert by_ref["recipe.demo@1"].spec_path.name == "demo.toml"
    assert by_ref["recipe.demo@1"].spec_path.exists()
    assert by_ref["cp.demo@1"].priority == 7
    assert by_ref["cp.demo@1"].gate_position == "after"

    # flat catalog reflects the non-recipe refs.
    assert isinstance(catalog, CompileRecipePackCatalog)
    assert catalog.tool_refs == ("DemoTool",)
    assert catalog.validator_refs == ("validator:demo@1",)
    assert catalog.plugin_refs == ("cp.demo@1",)
    # recipe ref not in any catalog tuple
    assert "recipe.demo@1" not in catalog.tool_refs + catalog.plugin_refs


def test_pipeline_missing_base_yields_empty():
    sink = RecordingSink()
    result, catalog = load_from_bases([Path("/nonexistent/base")], sink)
    assert result.primitives == ()
    assert catalog.tool_refs == ()
    assert sink.registered == []
