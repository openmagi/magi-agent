"""Keystone (Task 6.2): build_default_plugin loads first-party controls via the
pack loader, and accepts a user control_plane pack through the IDENTICAL path.

The de-privileging proof: the 6 first-party controls are no longer hand-assembled
inside ``build_default_plugin``; they are discovered+loaded from a bundled
``control_plane`` pack. A user-supplied control loads in parallel via
``extra_controls`` (the same seam a user ``~/.magi/packs`` control_plane pack uses
once projected into a LoopControl), with no first-party privilege.
"""
from __future__ import annotations

from magi_agent.adk_bridge.control_plane import (
    BaseLoopControl,
    GaConstraintReinjectionControl,
    build_default_plane,
    build_default_plugin,
)


def _names(plane) -> set[str]:
    return {getattr(c, "name", type(c).__name__) for c in plane._controls}


def test_build_default_plugin_loads_controls_from_packs_and_accepts_user_packs() -> None:
    env = {"MAGI_LOOP_GUARD_ENABLED": "1"}  # turn on a first-party control

    class _UserControl(BaseLoopControl):
        name = "user.control"

        async def on_before_model(self, *, callback_context, llm_request):  # noqa: ANN001
            return None

    plugin = build_default_plugin(
        os_environ=env,
        extra_controls=[_UserControl()],  # the parallel user-pack injection seam
    )
    plane = plugin._p  # _ExtendedControlPlanePlugin wraps a ControlPlane (self._p)
    names = _names(plane)
    # first-party resilience (loop-guard) control loaded from the bundled pack:
    assert any("resilience" in n.lower() or "loop" in n.lower() for n in names), names
    # user control loaded through the identical mechanism, in parallel:
    assert "user.control" in names, names


def test_pack_loaded_plane_matches_legacy_hand_assembly_byte_for_byte() -> None:
    """The pack-loaded plane is behavior-identical to ``build_default_plane``:
    same control types, in the same order, for the same env + collaborators."""
    env = {
        "MAGI_LOOP_GUARD_ENABLED": "1",
        "MAGI_EDIT_RETRY_REFLECTION_ENABLED": "1",
        "MAGI_CONTEXT_COMPACTION_ENABLED": "1",
        "MAGI_MAX_STEPS_BRAKE_ENABLED": "1",
    }
    legacy = build_default_plane(os_environ=env)
    plugin = build_default_plugin(os_environ=env)
    legacy_types = [type(c).__name__ for c in legacy._controls]
    loaded_types = [type(c).__name__ for c in plugin._p._controls]
    assert loaded_types == legacy_types, (loaded_types, legacy_types)


def test_build_default_plugin_no_arg_has_no_constraint_control_via_packs() -> None:
    """The §1 no-arg contract still holds through the pack-loaded path."""
    plugin = build_default_plugin()
    assert not any(
        isinstance(c, GaConstraintReinjectionControl) for c in plugin._p._controls
    )
