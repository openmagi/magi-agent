"""Group F.2 — a USER callback pack can ADD / OVERRIDE / REMOVE callback refs with
no first-party privilege (§1).

  * ADD     = a new user-sourced hook;
  * OVERRIDE = ``HookRegistry.replace`` keeps the new manifest when the existing
    one is not protected (``turn-audit`` is ``opt_out=True`` native-plugin →
    protected=False) — the description is free to change;
  * REMOVE  = ``[packs] disable`` by pack_id (a ``native-plugin`` hook is
    unregister-protected, so removal is realised as pack-disable — the hook never
    reaches the registry).
"""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.registries import load_into_registries

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"

_IMPL_BODY = (
    "from magi_agent.hooks.manifest import HookManifest, HookPoint\n"
    "from magi_agent.hooks.result import HookResult\n"
    "from magi_agent.tools.manifest import ToolSource\n"
    "_SRC = ToolSource(kind='custom-plugin', package='user_cb')\n"
    "def _h(ctx):\n    return HookResult(action='continue')\n"
    "def provide_my(ctx):\n"
    "    ctx.register(HookManifest(name='my-marker', point=HookPoint.AFTER_TURN_END,\n"
    "        description='user marker', source=_SRC, blocking=False), _h)\n"
    "def provide_override(ctx):\n"
    "    ctx.register(HookManifest(name='turn-audit', point=HookPoint.BEFORE_TURN_START,\n"
    "        description='OVERRIDDEN audit', source=_SRC, blocking=False), _h)\n"
    "def provide_removable(ctx):\n"
    "    ctx.register(HookManifest(name='removable-cb', point=HookPoint.AFTER_TURN_END,\n"
    "        description='rm', source=_SRC, blocking=False), _h)\n"
)


def _write_pack(root: Path, name: str, pack_id: str, provides: str) -> None:
    pack_dir = root / name
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "impl.py").write_text(_IMPL_BODY)
    (pack_dir / "pack.toml").write_text(
        f"packId = {pack_id!r}\ndisplayName = {pack_id!r}\nversion = \"0.0.1\"\n\n" + provides
    )


def test_user_callback_add_override_remove(tmp_path: Path, monkeypatch) -> None:
    user_root = tmp_path / "user_packs"
    _write_pack(
        user_root, "user_cb", "user.cb",
        "[[provides]]\ntype = \"callback\"\nref = \"my-marker\"\nimpl = \"user_cb.impl:provide_my\"\nphase = \"afterTurnEnd\"\n\n"
        "[[provides]]\ntype = \"callback\"\nref = \"turn-audit\"\nimpl = \"user_cb.impl:provide_override\"\nphase = \"beforeTurnStart\"\n",
    )
    _write_pack(
        user_root, "user_cb_rm", "user.cb-rm",
        "[[provides]]\ntype = \"callback\"\nref = \"removable-cb\"\nimpl = \"user_cb_rm.impl:provide_removable\"\nphase = \"afterTurnEnd\"\n",
    )
    monkeypatch.syspath_prepend(str(user_root))
    config_path = tmp_path / "config.toml"
    config_path.write_text('[packs]\ndisable = ["user.cb-rm"]\n')
    monkeypatch.setenv("MAGI_CONFIG", str(config_path))

    registries, _ = load_into_registries([_FIRST_PARTY_ROOT, user_root])

    assert registries.hooks.resolve("my-marker") is not None  # ADD
    assert registries.hooks.resolve("turn-audit").description == "OVERRIDDEN audit"  # OVERRIDE
    assert registries.hooks.resolve("removable-cb") is None  # REMOVE
