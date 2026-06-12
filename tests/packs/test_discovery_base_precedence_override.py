"""BUG 1 — base order is the override precedence; a global pack_id sort must NOT
override it.

``discover_pack_files`` builds ``discovered`` base-by-base in precedence order
(bundled first-party, then ``~/.magi/packs``, then ``<cwd>/.magi/packs``) where a
LATER base wins via downstream last-wins registration. A global
``discovered.sort(key=pack_id)`` (and the equivalent rest-sort in
``resolve_enabled_packs``) destroys that base precedence: a USER pack whose
``pack_id`` sorts alphabetically BEFORE the bundled pack's would be loaded FIRST
and then OVERWRITTEN by the first-party impl — the exact opposite of the intended
override.

Realistic scenario: a user authors ``~/.magi/packs/...`` with ``packId =
"aaa.user-clock"`` overriding the bundled ``openmagi.tools-clock`` Clock. The
user's base is higher precedence (passed last), so the user's Clock must win
regardless of the alphabetical relationship of the two pack ids.
"""
from __future__ import annotations

from pathlib import Path

from magi_agent.packs.discovery import (
    discover_pack_files,
    load_packs_config,
    resolve_enabled_packs,
)
from magi_agent.packs.registries import load_into_registries

_IMPL_BODY = (
    "from magi_agent.packs.context import ToolProvideContext\n"
    "from magi_agent.tools.catalog import CORE_TOOL_SOURCE, CORE_TOOL_INPUT_SCHEMA\n"
    "from magi_agent.tools.manifest import Budget, ToolManifest\n"
    "def _mk(name, desc):\n"
    "    return ToolManifest(name=name, description=desc, kind='core',\n"
    "        source=CORE_TOOL_SOURCE, permission='meta',\n"
    "        input_schema=CORE_TOOL_INPUT_SCHEMA, timeout_ms=10_000,\n"
    "        budget=Budget(max_calls_per_turn=3, max_parallel=1), dangerous=False,\n"
    "        is_concurrency_safe=True, mutates_workspace=False,\n"
    "        parallel_safety='readonly', available_in_modes=('plan','act'),\n"
    "        tags=('utility','meta'), enabled_by_default=True, opt_out=True)\n"
    "def provide_clock_a(ctx):\n"
    "    ctx.register(_mk('Clock', 'BASE_A clock.'))\n"
    "def provide_clock_b(ctx):\n"
    "    ctx.register(_mk('Clock', 'BASE_B clock.'))\n"
)


def _write_pack(root: Path, pkg: str, pack_id: str, provide_symbol: str) -> None:
    pack_dir = root / pkg
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "impl.py").write_text(_IMPL_BODY)
    (pack_dir / "pack.toml").write_text(
        f"packId = {pack_id!r}\ndisplayName = {pack_id!r}\nversion = \"0.0.1\"\n\n"
        "[[provides]]\ntype = \"tool\"\nref = \"Clock\"\n"
        f"impl = \"{pkg}.impl:{provide_symbol}\"\n"
    )


def test_discover_pack_files_preserves_base_order(tmp_path: Path) -> None:
    """``discover_pack_files`` must list packs in BASE order, not pack_id order."""
    base_a = tmp_path / "base_a"
    base_b = tmp_path / "base_b"
    _write_pack(base_a, "clk_a", "openmagi.tools-clock", "provide_clock_a")
    _write_pack(base_b, "clk_b", "aaa.user-clock", "provide_clock_b")

    discovered = discover_pack_files([base_a, base_b])
    pack_ids = [d.manifest.pack_id for d in discovered]
    # base_a is passed first; base_b second. The list MUST follow base order,
    # so the higher-precedence base_b pack is LAST and wins downstream last-wins.
    assert pack_ids == ["openmagi.tools-clock", "aaa.user-clock"], pack_ids


def test_resolve_enabled_packs_preserves_base_order(tmp_path: Path) -> None:
    """``resolve_enabled_packs`` (no pins) must keep discovered/base order."""
    base_a = tmp_path / "base_a"
    base_b = tmp_path / "base_b"
    _write_pack(base_a, "clk_a", "openmagi.tools-clock", "provide_clock_a")
    _write_pack(base_b, "clk_b", "aaa.user-clock", "provide_clock_b")

    discovered = discover_pack_files([base_a, base_b])
    enabled = resolve_enabled_packs(discovered, load_packs_config())
    pack_ids = [d.manifest.pack_id for d in enabled]
    assert pack_ids == ["openmagi.tools-clock", "aaa.user-clock"], pack_ids


def test_later_base_wins_regardless_of_pack_id_alpha(
    tmp_path: Path, monkeypatch
) -> None:
    """End-to-end: the higher-precedence (later) base wins the Clock override even
    though its pack_id ("aaa.user-clock") sorts alphabetically before the bundled
    one ("openmagi.tools-clock")."""
    base_a = tmp_path / "base_a"
    base_b = tmp_path / "base_b"
    _write_pack(base_a, "clk_a", "openmagi.tools-clock", "provide_clock_a")
    _write_pack(base_b, "clk_b", "aaa.user-clock", "provide_clock_b")
    monkeypatch.syspath_prepend(str(base_a))
    monkeypatch.syspath_prepend(str(base_b))

    config_path = tmp_path / "config.toml"
    config_path.write_text("[packs]\n")
    monkeypatch.setenv("MAGI_CONFIG", str(config_path))

    registries, _ = load_into_registries([base_a, base_b])

    clock = registries.tools.resolve("Clock")
    assert clock is not None
    # base_b is the higher-precedence base (passed last) -> its impl must win.
    assert clock.description == "BASE_B clock.", clock.description
