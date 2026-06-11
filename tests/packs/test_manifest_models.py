import pytest
from pydantic import ValidationError

from magi_agent.packs.manifest import PackManifest, ProvidesEntry


def test_provides_entry_tool_code_impl():
    entry = ProvidesEntry.model_validate(
        {"type": "tool", "ref": "FileWrite", "impl": "pkg.mod:FileWriteTool"}
    )
    assert entry.type == "tool"
    assert entry.ref == "FileWrite"
    assert entry.impl == "pkg.mod:FileWriteTool"
    assert entry.spec is None
    # ordering metadata defaults only meaningful for ordered types; None here
    assert entry.priority is None
    assert entry.phase is None
    assert entry.gate_position is None


def test_provides_entry_recipe_uses_spec_not_impl():
    entry = ProvidesEntry.model_validate(
        {"type": "recipe", "ref": "recipe.research@1", "spec": "recipes/research.toml"}
    )
    assert entry.spec == "recipes/research.toml"
    assert entry.impl is None


def test_provides_entry_rejects_both_impl_and_spec():
    with pytest.raises(ValidationError):
        ProvidesEntry.model_validate(
            {"type": "tool", "ref": "X", "impl": "a:b", "spec": "c.toml"}
        )


def test_provides_entry_rejects_neither_impl_nor_spec():
    with pytest.raises(ValidationError):
        ProvidesEntry.model_validate({"type": "tool", "ref": "X"})


def test_recipe_must_use_spec_code_types_must_use_impl():
    # recipe with impl is invalid
    with pytest.raises(ValidationError):
        ProvidesEntry.model_validate({"type": "recipe", "ref": "r", "impl": "a:b"})
    # tool with spec is invalid
    with pytest.raises(ValidationError):
        ProvidesEntry.model_validate({"type": "tool", "ref": "t", "spec": "r.toml"})


def test_provides_entry_rejects_unknown_type():
    with pytest.raises(ValidationError):
        ProvidesEntry.model_validate({"type": "wizard", "ref": "X", "impl": "a:b"})


def test_impl_must_be_module_colon_symbol():
    with pytest.raises(ValidationError):
        ProvidesEntry.model_validate({"type": "tool", "ref": "X", "impl": "no_colon"})


def test_callback_carries_priority_and_phase_via_camelcase_alias():
    entry = ProvidesEntry.model_validate(
        {"type": "callback", "ref": "cb.audit@1", "impl": "a:b",
         "priority": 10, "phase": "before_model"}
    )
    assert entry.priority == 10
    assert entry.phase == "before_model"


def test_control_plane_gate_position_defaults_to_after():
    entry = ProvidesEntry.model_validate(
        {"type": "control_plane", "ref": "cp.maxsteps@1", "impl": "a:b", "priority": 5}
    )
    assert entry.gate_position == "after"


def test_control_plane_gate_position_explicit_before():
    entry = ProvidesEntry.model_validate(
        {"type": "control_plane", "ref": "cp.gate@1", "impl": "a:b",
         "priority": 5, "gatePosition": "before"}
    )
    assert entry.gate_position == "before"


def test_gate_position_only_allowed_on_control_plane():
    with pytest.raises(ValidationError):
        ProvidesEntry.model_validate(
            {"type": "tool", "ref": "t", "impl": "a:b", "gatePosition": "before"}
        )


def test_priority_phase_only_on_ordered_types():
    # validator is unordered -> priority forbidden
    with pytest.raises(ValidationError):
        ProvidesEntry.model_validate(
            {"type": "validator", "ref": "v", "impl": "a:b", "priority": 3}
        )


def test_models_are_frozen_and_forbid_extra():
    entry = ProvidesEntry.model_validate({"type": "tool", "ref": "X", "impl": "a:b"})
    with pytest.raises(ValidationError):
        entry.ref = "Y"  # frozen
    with pytest.raises(ValidationError):
        ProvidesEntry.model_validate(
            {"type": "tool", "ref": "X", "impl": "a:b", "junk": 1}
        )


def test_pack_manifest_parses_provides_list():
    manifest = PackManifest.model_validate(
        {
            "packId": "firstparty.tools",
            "version": "1",
            "displayName": "First-party tools",
            "provides": [
                {"type": "tool", "ref": "FileWrite", "impl": "m:FileWrite"},
                {"type": "validator", "ref": "validator:x@1", "impl": "m:VX"},
            ],
        }
    )
    assert manifest.pack_id == "firstparty.tools"
    assert manifest.version == "1"
    assert len(manifest.provides) == 2
    assert manifest.provides[0].type == "tool"


def test_pack_manifest_rejects_duplicate_refs_within_pack():
    with pytest.raises(ValidationError):
        PackManifest.model_validate(
            {
                "packId": "p",
                "displayName": "p",
                "provides": [
                    {"type": "tool", "ref": "Dup", "impl": "m:A"},
                    {"type": "tool", "ref": "Dup", "impl": "m:B"},
                ],
            }
        )
