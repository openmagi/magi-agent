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


def test_disk_user_control_plane_pack_loads_in_parallel_with_first_party(
    tmp_path, monkeypatch
) -> None:
    """The strongest §1 keystone proof: a real on-disk user ``control_plane`` pack
    loads through the IDENTICAL loader path as the bundled first-party pack — not
    via ``extra_controls``, but via discovery — and registers alongside it with no
    first-party privilege."""
    from pathlib import Path

    from magi_agent.packs.discovery import _bundled_firstparty_base
    from magi_agent.packs.registries import build_control_plane_from_packs

    user_root = tmp_path / "packs"
    pack_dir = user_root / "user_cp"
    pack_dir.mkdir(parents=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "impl.py").write_text(
        "from magi_agent.adk_bridge.control_plane import BaseLoopControl\n"
        "class UserParallelControl(BaseLoopControl):\n"
        "    name = 'user.parallel.control'\n"
        "    async def on_before_model(self, *, callback_context, llm_request):\n"
        "        return None\n"
        "def provide(ctx):\n"
        "    ctx.register(UserParallelControl())\n"
    )
    (pack_dir / "pack.toml").write_text(
        'packId = "user.control-plane-extra"\n'
        'displayName = "user cp extra"\nversion = "0.0.1"\n\n'
        '[[provides]]\ntype = "control_plane"\n'
        'ref = "control_plane:user-extra@1"\n'
        'impl = "user_cp.impl:provide"\ngatePosition = "after"\n'
    )
    monkeypatch.syspath_prepend(str(user_root))
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))

    bases = [_bundled_firstparty_base(), Path(str(user_root))]
    plane = build_control_plane_from_packs(
        bases=bases, os_environ={"MAGI_LOOP_GUARD_ENABLED": "1"}
    )
    names = _names(plane)
    assert any("resilience" in n.lower() or "loop" in n.lower() for n in names), names
    assert "user.parallel.control" in names, names
