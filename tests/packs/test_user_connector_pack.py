"""Group D.2 — a USER connector pack can ADD / OVERRIDE / REMOVE connector refs
with no first-party privilege (§1). Override = last-wins load order; remove =
``[packs] disable`` by pack_id."""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.registries import load_into_registries

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"

_IMPL_BODY = (
    "from magi_agent.packs.context import ConnectorProvideContext, ConnectorSpec\n"
    "def provide_my(ctx):\n"
    "    ctx.register('connector:my-mcp@1', ConnectorSpec(server_ref='my-mcp', readonly=True, tool_manifests=()))\n"
    "def provide_override(ctx):\n"
    "    ctx.register('connector:local-readonly@1', ConnectorSpec(server_ref='local-readonly-v2', readonly=True, tool_manifests=()))\n"
    "def provide_removable(ctx):\n"
    "    ctx.register('connector:removable@1', ConnectorSpec(server_ref='rm', readonly=True, tool_manifests=()))\n"
)


def _write_pack(root: Path, name: str, pack_id: str, provides: str) -> None:
    pack_dir = root / name
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "impl.py").write_text(_IMPL_BODY)
    (pack_dir / "pack.toml").write_text(
        f"packId = {pack_id!r}\ndisplayName = {pack_id!r}\nversion = \"0.0.1\"\n\n" + provides
    )


def test_user_connector_add_override_remove(tmp_path: Path, monkeypatch) -> None:
    user_root = tmp_path / "user_packs"
    _write_pack(
        user_root, "user_conn", "user.conn",
        "[[provides]]\ntype = \"connector\"\nref = \"connector:my-mcp@1\"\nimpl = \"user_conn.impl:provide_my\"\n\n"
        "[[provides]]\ntype = \"connector\"\nref = \"connector:local-readonly@1\"\nimpl = \"user_conn.impl:provide_override\"\n",
    )
    _write_pack(
        user_root, "user_conn_rm", "user.conn-rm",
        "[[provides]]\ntype = \"connector\"\nref = \"connector:removable@1\"\nimpl = \"user_conn_rm.impl:provide_removable\"\n",
    )
    monkeypatch.syspath_prepend(str(user_root))
    config_path = tmp_path / "config.toml"
    config_path.write_text('[packs]\ndisable = ["user.conn-rm"]\n')
    monkeypatch.setenv("MAGI_CONFIG", str(config_path))

    registries, _ = load_into_registries([_FIRST_PARTY_ROOT, user_root])

    assert registries.connectors.resolve("connector:my-mcp@1") is not None  # ADD
    assert (
        registries.connectors.resolve("connector:local-readonly@1").server_ref
        == "local-readonly-v2"
    )  # OVERRIDE
    assert registries.connectors.resolve("connector:removable@1") is None  # REMOVE
