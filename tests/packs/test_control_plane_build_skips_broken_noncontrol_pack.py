"""BUG 2 — a broken NON-control pack must not break control-plane assembly.

``build_control_plane_from_packs`` only consumes ``control_plane`` provides
entries, but it called ``load_packs(enabled, sink)`` first — which lazily imports
EVERY enabled pack's impl — and only afterward filtered to ``control_plane``. So
an enabled tool pack with a broken/missing-dependency impl made ``load_packs``
raise, ``build_default_plugin`` failed, and the runner could not start, even
though that tool pack contributes nothing to the control plane.

Realistic scenario: a user drops a tool pack into ``~/.magi/packs`` whose impl
imports a package they have not installed. That should disable/skip that one tool
(or surface at tool resolution), not nuke the whole control plane and refuse to
boot the runner.
"""
from __future__ import annotations

from pathlib import Path

from magi_agent.packs.discovery import _bundled_firstparty_base
from magi_agent.packs.registries import build_control_plane_from_packs

# An impl module that explodes at IMPORT time (missing dependency), exactly like a
# user tool pack that imports a package they never installed.
_BROKEN_IMPL = "import this_module_does_not_exist_anywhere_xyz  # noqa: F401\n"


def _write_broken_tool_pack(root: Path) -> Path:
    pack_dir = root / "broken_tool"
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "impl.py").write_text(_BROKEN_IMPL)
    (pack_dir / "pack.toml").write_text(
        'packId = "user.broken-tool"\n'
        'displayName = "Broken tool"\n'
        'version = "0.0.1"\n\n'
        "[[provides]]\n"
        'type = "tool"\n'
        'ref = "BrokenTool"\n'
        'impl = "broken_tool.impl:provide_broken"\n'
    )
    return root


def test_control_plane_builds_despite_broken_noncontrol_pack(
    tmp_path: Path, monkeypatch
) -> None:
    user_root = tmp_path / "user_packs"
    _write_broken_tool_pack(user_root)
    monkeypatch.syspath_prepend(str(user_root))

    config_path = tmp_path / "config.toml"
    config_path.write_text("[packs]\n")
    monkeypatch.setenv("MAGI_CONFIG", str(config_path))

    # The bundled control packs PLUS the broken tool pack are both "enabled".
    plane = build_control_plane_from_packs(
        bases=[_bundled_firstparty_base(), user_root],
        os_environ={},
    )

    # The control plane still assembled: the bundled default control is present.
    names = [getattr(c, "name", type(c).__name__) for c in plane._controls]
    assert names, "control plane built no controls"
    # The broken tool pack contributes no control and must not appear.
    assert "BrokenTool" not in names
