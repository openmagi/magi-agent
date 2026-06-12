"""Group A.2 — a USER tool pack can ADD a new ref, OVERRIDE a first-party ref,
and REMOVE (forbid) a ref, with NO first-party privilege (§1).

Adapted to the real Phase-1/2/3 ABI:
  * discovery/override = ``discover_pack_files`` + ``resolve_enabled_packs`` (a
    ``user.*`` pack_id sorts AFTER ``openmagi.*`` so the user impl wins last-wins);
  * remove/forbid = ``config.toml [packs] disable = ["<pack_id>"]`` keyed by
    PACK_ID (the kernel manifest rejects an impl-less ``"-"``-prefix entry, so the
    doc's dash convention is realised as pack-disable — the Phase-3 convention).
  * override stays metadata-preserving: ``Clock`` is registered ``kind="core"``
    (protected), so the user override keeps ``kind="core"``/``CORE_TOOL_SOURCE``
    and only changes the description (a non-downgrade field).
"""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.registries import load_into_registries

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"


_IMPL_BODY = (
    "from magi_agent.packs.context import ToolProvideContext\n"
    "from magi_agent.tools.catalog import CORE_TOOL_SOURCE, CORE_TOOL_INPUT_SCHEMA\n"
    "from magi_agent.tools.manifest import Budget, ToolManifest\n"
    "def _mk(name, desc, kind='core', source=None):\n"
    "    return ToolManifest(name=name, description=desc, kind=kind,\n"
    "        source=source or CORE_TOOL_SOURCE, permission='meta',\n"
    "        input_schema=CORE_TOOL_INPUT_SCHEMA, timeout_ms=10_000,\n"
    "        budget=Budget(max_calls_per_turn=3, max_parallel=1), dangerous=False,\n"
    "        is_concurrency_safe=True, mutates_workspace=False,\n"
    "        parallel_safety='readonly', available_in_modes=('plan','act'),\n"
    "        tags=('utility','meta'), enabled_by_default=True, opt_out=True)\n"
    "def provide_weather(ctx):\n"
    "    ctx.register(_mk('WeatherPeek', 'Peek the weather.', kind='external',\n"
    "        source=__import__('magi_agent.tools.manifest', fromlist=['ToolSource'])"
    ".ToolSource(kind='custom-plugin', package='user_tools')))\n"
    "def provide_clock_override(ctx):\n"
    "    ctx.register(_mk('Clock', 'OVERRIDDEN clock.'))\n"
    "def provide_calc(ctx):\n"
    "    ctx.register(_mk('Calculation', 'calc', kind='external',\n"
    "        source=__import__('magi_agent.tools.manifest', fromlist=['ToolSource'])"
    ".ToolSource(kind='custom-plugin', package='user_tools')))\n"
)


def _write_pack(root: Path, name: str, pack_id: str, body: str) -> None:
    pack_dir = root / name
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "impl.py").write_text(_IMPL_BODY)
    (pack_dir / "pack.toml").write_text(
        f"packId = {pack_id!r}\ndisplayName = {pack_id!r}\nversion = \"0.0.1\"\n\n" + body
    )


def test_user_tool_pack_add_override_remove(tmp_path: Path, monkeypatch) -> None:
    user_root = tmp_path / "user_packs"
    # ADD WeatherPeek + OVERRIDE Clock (one pack, sorts after openmagi.*)
    _write_pack(
        user_root, "user_tools", "user.tools",
        "[[provides]]\ntype = \"tool\"\nref = \"WeatherPeek\"\n"
        f"impl = \"user_tools.impl:provide_weather\"\n\n"
        "[[provides]]\ntype = \"tool\"\nref = \"Clock\"\n"
        f"impl = \"user_tools.impl:provide_clock_override\"\n",
    )
    # A separate removable Calculation pack we will disable by pack_id.
    _write_pack(
        user_root, "user_calc", "user.calc",
        "[[provides]]\ntype = \"tool\"\nref = \"Calculation\"\n"
        f"impl = \"user_calc.impl:provide_calc\"\n",
    )
    monkeypatch.syspath_prepend(str(user_root))
    config_path = tmp_path / "config.toml"
    config_path.write_text('[packs]\ndisable = ["user.calc"]\n')
    monkeypatch.setenv("MAGI_CONFIG", str(config_path))

    registries, _ = load_into_registries([_FIRST_PARTY_ROOT, user_root])

    # ADD
    assert registries.tools.resolve("WeatherPeek") is not None
    # OVERRIDE — user impl won (no first-party privilege)
    assert registries.tools.resolve("Clock").description == "OVERRIDDEN clock."
    # REMOVE — the disabled pack's Calculation never reached the registry
    assert registries.tools.resolve("Calculation") is None
