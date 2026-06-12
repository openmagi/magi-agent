"""Pack B0 — a pure disk drop of a user pack MUST import with ZERO env setup.

Measured DX gap (real-model QA): a pack at <root>/user_cp_zero with
``impl = "user_cp_zero.impl:provide"`` needed a manual PYTHONPATH export before
the loader could import it. The loader must auto-inject the discovered pack's
parent dir into ``sys.path`` (idempotent, append — installed packages keep
winning on a name collision) so ``magi pack new`` output and ~/.magi/packs
drops Just Work.

Module names here are UNIQUE to this file (``user_cp_zero``, not the keystone
test's ``user_cp``) so the zero-setup proof cannot be satisfied vacuously by a
``sys.modules`` entry cached by an earlier test in the same session.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from magi_agent.packs.loader import RecordingSink, lazy_import_symbol, load_from_bases

# Top-level module names created on disk by tests in this file. The autouse
# fixture below purges them from sys.modules at teardown so the loader's
# auto-injection cannot leak tmp-dir modules into unrelated tests.
_TEST_MODULE_PREFIXES = ("user_cp_zero", "pack_alpha", "pack_beta", "sym_pack_zero")


@pytest.fixture(autouse=True)
def _isolate_import_state():
    """Snapshot sys.path and purge this file's disk modules after each test."""
    saved_path = [*sys.path]
    yield
    sys.path[:] = saved_path
    for name in [
        m for m in sys.modules if m.split(".", 1)[0] in _TEST_MODULE_PREFIXES
    ]:
        sys.modules.pop(name, None)


def _write_user_cp_pack(root: Path) -> Path:
    """The user_cp shape from the §1 keystone test (external module path)."""
    pack_dir = root / "user_cp_zero"
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
        'impl = "user_cp_zero.impl:provide"\ngatePosition = "after"\n'
    )
    return pack_dir


def test_disk_pack_imports_with_zero_syspath_setup(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    # NOTE: deliberately NO monkeypatch.syspath_prepend — that is the gap.
    root = tmp_path / "packs"
    _write_user_cp_pack(root)
    assert "user_cp_zero" not in sys.modules  # the proof must not be cached

    result, _catalog = load_from_bases([root], RecordingSink())

    primitives = {(p.type, p.ref): p for p in result.primitives}
    assert ("control_plane", "control_plane:user-extra@1") in primitives
    assert callable(primitives[("control_plane", "control_plane:user-extra@1")].impl)


def test_two_packs_each_with_impl_py_do_not_collide(tmp_path, monkeypatch) -> None:
    """No import leakage across packs: two packs both shipping an ``impl.py``
    (the standard scaffold layout) must each resolve their OWN module — the
    auto-injected roots must not let one pack's ``impl`` shadow the other's."""
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    base_a = tmp_path / "base_a"
    base_b = tmp_path / "base_b"
    for base, mod, marker in (
        (base_a, "pack_alpha", "alpha-marker"),
        (base_b, "pack_beta", "beta-marker"),
    ):
        pack_dir = base / mod
        pack_dir.mkdir(parents=True)
        (pack_dir / "__init__.py").write_text("")
        (pack_dir / "impl.py").write_text(
            f"def provide(ctx):\n    return {marker!r}\n"
        )
        (pack_dir / "pack.toml").write_text(
            f'packId = "user.{mod.replace("_", "-")}"\n'
            f'displayName = "{mod}"\nversion = "0.0.1"\n\n'
            '[[provides]]\ntype = "control_plane"\n'
            f'ref = "control_plane:{mod.replace("_", "-")}@1"\n'
            f'impl = "{mod}.impl:provide"\ngatePosition = "after"\n'
        )

    result, _catalog = load_from_bases([base_a, base_b], RecordingSink())

    by_ref = {p.ref: p.impl for p in result.primitives}
    impl_a = by_ref["control_plane:pack-alpha@1"]
    impl_b = by_ref["control_plane:pack-beta@1"]
    assert impl_a.__module__ == "pack_alpha.impl"
    assert impl_b.__module__ == "pack_beta.impl"
    assert impl_a(None) == "alpha-marker"
    assert impl_b(None) == "beta-marker"


def test_missing_symbol_still_raises_import_error(tmp_path) -> None:
    """Auto-injection must not swallow real errors: module found, symbol absent."""
    pack_dir = tmp_path / "sym_pack_zero"
    pack_dir.mkdir(parents=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "impl.py").write_text("def provide(ctx):\n    pass\n")
    with pytest.raises(ImportError, match="not found in module"):
        lazy_import_symbol("sym_pack_zero.impl:nope", search_root=tmp_path)


def test_unrelated_missing_module_still_raises(tmp_path) -> None:
    """A module path that does NOT live under the pack root must not be retried."""
    with pytest.raises(ModuleNotFoundError):
        lazy_import_symbol(
            "definitely_not_a_real_module_xyz.impl:provide", search_root=tmp_path
        )
