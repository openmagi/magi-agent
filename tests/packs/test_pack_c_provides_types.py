"""Pack C shared surface (Task C0): 3 new provides types + workspace-handler seam.

The new types follow the exact ``harness`` pattern (Literal entry +
``PrimitiveType`` member + ``KeyedRefRegistry`` slot + frozen provide-context +
``project_into_registries`` branch). The ``tool`` type additionally gains
``register_workspace_handler`` — the seam C1 binds gate5b tool impls through.
"""
from __future__ import annotations

from pathlib import Path

from magi_agent.packs.manifest import PackManifest


def _manifest(ptype: str, ref: str, impl: str) -> PackManifest:
    return PackManifest.model_validate(
        {
            "packId": "test.pack-c",
            "displayName": "pack c fixture",
            "provides": [{"type": ptype, "ref": ref, "impl": impl}],
        }
    )


def test_manifest_accepts_the_three_pack_c_types():
    for ptype, ref in (
        ("loop_policy", "loop_policy:fake@1"),
        ("schedule_policy", "schedule_policy:fake@1"),
        ("memory_strategy", "memory_strategy:fake@1"),
    ):
        m = _manifest(ptype, ref, "tests.packs.pack_c_fixture_impls:provide_loop_policy")
        assert m.provides[0].type == ptype


def test_primitive_type_enum_has_pack_c_members():
    from magi_agent.packs.context import PrimitiveType

    assert PrimitiveType.LOOP_POLICY.value == "loop_policy"
    assert PrimitiveType.SCHEDULE_POLICY.value == "schedule_policy"
    assert PrimitiveType.MEMORY_STRATEGY.value == "memory_strategy"


def test_load_into_registries_projects_pack_c_types_and_workspace_handler(tmp_path: Path):
    """End-to-end through the REAL discover -> resolve -> load -> project pipeline
    (same path a ~/.magi/packs user pack takes — §1 no privilege)."""
    from magi_agent.packs.registries import load_into_registries

    pack_dir = tmp_path / "pack-c-fixture"
    pack_dir.mkdir()
    (pack_dir / "pack.toml").write_text(
        'packId = "test.pack-c-fixture"\n'
        'displayName = "pack c fixture"\n'
        "\n"
        "[[provides]]\n"
        'type = "loop_policy"\n'
        'ref = "loop_policy:fake@1"\n'
        'impl = "tests.packs.pack_c_fixture_impls:provide_loop_policy"\n'
        "\n"
        "[[provides]]\n"
        'type = "schedule_policy"\n'
        'ref = "schedule_policy:fake@1"\n'
        'impl = "tests.packs.pack_c_fixture_impls:provide_schedule_policy"\n'
        "\n"
        "[[provides]]\n"
        'type = "memory_strategy"\n'
        'ref = "memory_strategy:fake@1"\n'
        'impl = "tests.packs.pack_c_fixture_impls:provide_memory_strategy"\n'
        "\n"
        "[[provides]]\n"
        'type = "tool"\n'
        'ref = "workspace:FakeTool@1"\n'
        'impl = "tests.packs.pack_c_fixture_impls:provide_workspace_handler"\n'
    )
    registries, report = load_into_registries([tmp_path])
    assert "loop_policy:fake@1" in report.registered
    assert registries.loop_policies.resolve("loop_policy:fake@1") is not None
    assert registries.schedule_policies.resolve("schedule_policy:fake@1") is not None
    assert registries.memory_strategies.resolve("memory_strategy:fake@1") is not None
    # The tool provider registered a WORKSPACE handler (keyed by tool name).
    handler = registries.workspace_tool_handlers.resolve("FakeTool")
    assert callable(handler)
    assert handler({"x": 1}, None) == {"echo": {"x": 1}}
