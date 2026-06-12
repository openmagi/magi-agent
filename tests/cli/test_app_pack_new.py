"""CliRunner tests for the `magi pack new` scaffolding subcommand (Pack B1)."""
from __future__ import annotations

import sys


def _make_app():
    from magi_agent.cli.app import app
    return app


def test_pack_new_scaffolds_a_loadable_validator_pack(tmp_path, monkeypatch) -> None:
    from typer.testing import CliRunner

    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(sys, "path", [*sys.path])  # revert loader auto-injection
    runner = CliRunner()
    result = runner.invoke(
        _make_app(),
        ["pack", "new", "validator", "my-check", "--dest", str(tmp_path / "packs")],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    pack_dir = tmp_path / "packs" / "my_check"
    assert (pack_dir / "pack.toml").is_file()
    assert (pack_dir / "impl.py").is_file()
    assert (pack_dir / "test_my_check_pack.py").is_file()
    assert "pack created" in result.output

    from magi_agent.packs.loader import RecordingSink, load_from_bases

    loaded, _catalog = load_from_bases([tmp_path / "packs"], RecordingSink())
    assert any(p.ref == "verifier:myCheck@1" for p in loaded.primitives)


def test_pack_new_unknown_type_exits_2(tmp_path) -> None:
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(
        _make_app(), ["pack", "new", "widget", "x", "--dest", str(tmp_path / "packs")]
    )
    assert result.exit_code == 2
    assert "unknown pack type" in result.output


def test_pack_root_prints_usage(tmp_path) -> None:
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(_make_app(), ["pack"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "magi pack new" in result.output
