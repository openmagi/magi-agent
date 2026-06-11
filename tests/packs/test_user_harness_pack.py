"""Group E.2 — a USER harness pack can ADD / OVERRIDE / REMOVE harness refs with
no first-party privilege (§1). Override = last-wins load order; remove =
``[packs] disable`` by pack_id."""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.registries import load_into_registries

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"

_IMPL_BODY = (
    "from magi_agent.harness.resolved import ResolvedHarnessPack\n"
    "def provide_my(ctx):\n"
    "    ctx.register('harness:my-pack@1', ResolvedHarnessPack(enabled=True, source='custom-plugin',\n"
    "        components={'tools': ('FileRead',)}))\n"
    "def provide_override(ctx):\n"
    "    ctx.register('harness:coding-lean@1', ResolvedHarnessPack(enabled=False, source='custom-plugin',\n"
    "        components={'tools': ('FileRead',)}))\n"
    "def provide_removable(ctx):\n"
    "    ctx.register('harness:removable@1', ResolvedHarnessPack(enabled=True, source='custom-plugin',\n"
    "        components={'tools': ()}))\n"
)


def _write_pack(root: Path, name: str, pack_id: str, provides: str) -> None:
    pack_dir = root / name
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "impl.py").write_text(_IMPL_BODY)
    (pack_dir / "pack.toml").write_text(
        f"packId = {pack_id!r}\ndisplayName = {pack_id!r}\nversion = \"0.0.1\"\n\n" + provides
    )


def test_user_harness_add_override_remove(tmp_path: Path, monkeypatch) -> None:
    user_root = tmp_path / "user_packs"
    _write_pack(
        user_root, "user_harness", "user.harness",
        "[[provides]]\ntype = \"harness\"\nref = \"harness:my-pack@1\"\nimpl = \"user_harness.impl:provide_my\"\n\n"
        "[[provides]]\ntype = \"harness\"\nref = \"harness:coding-lean@1\"\nimpl = \"user_harness.impl:provide_override\"\n",
    )
    _write_pack(
        user_root, "user_harness_rm", "user.harness-rm",
        "[[provides]]\ntype = \"harness\"\nref = \"harness:removable@1\"\nimpl = \"user_harness_rm.impl:provide_removable\"\n",
    )
    monkeypatch.syspath_prepend(str(user_root))
    config_path = tmp_path / "config.toml"
    config_path.write_text('[packs]\ndisable = ["user.harness-rm"]\n')
    monkeypatch.setenv("MAGI_CONFIG", str(config_path))

    registries, _ = load_into_registries([_FIRST_PARTY_ROOT, user_root])

    assert registries.harnesses.resolve("harness:my-pack@1") is not None  # ADD
    assert registries.harnesses.resolve("harness:coding-lean@1").enabled is False  # OVERRIDE
    assert registries.harnesses.resolve("harness:removable@1") is None  # REMOVE
