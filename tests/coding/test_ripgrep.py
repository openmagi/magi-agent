from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from magi_agent.coding import ripgrep


# --- rg_available + bin resolution --------------------------------------------


def test_rg_available_true_when_which_finds_binary(monkeypatch):
    monkeypatch.delenv("MAGI_RIPGREP_BIN", raising=False)
    monkeypatch.setattr(ripgrep.shutil, "which", lambda name: "/usr/bin/rg")
    assert ripgrep.rg_available() is True


def test_rg_available_false_when_which_returns_none(monkeypatch):
    monkeypatch.delenv("MAGI_RIPGREP_BIN", raising=False)
    monkeypatch.setattr(ripgrep.shutil, "which", lambda name: None)
    assert ripgrep.rg_available() is False


def test_rg_available_honors_bin_override_env(monkeypatch):
    seen: dict[str, str] = {}

    def fake_which(name: str):
        seen["name"] = name
        return "/custom/path/rg" if name == "/custom/path/rg" else None

    monkeypatch.setenv("MAGI_RIPGREP_BIN", "/custom/path/rg")
    monkeypatch.setattr(ripgrep.shutil, "which", fake_which)
    assert ripgrep.rg_available() is True
    assert seen["name"] == "/custom/path/rg"


def test_rg_available_bin_arg_overrides_env(monkeypatch):
    monkeypatch.setenv("MAGI_RIPGREP_BIN", "/env/rg")
    monkeypatch.setattr(
        ripgrep.shutil, "which", lambda name: "/arg/rg" if name == "/arg/rg" else None
    )
    assert ripgrep.rg_available(bin_path="/arg/rg") is True


def test_rg_available_missing_override_reports_false(monkeypatch):
    monkeypatch.setenv("MAGI_RIPGREP_BIN", "/nonexistent/rg")
    monkeypatch.setattr(ripgrep.shutil, "which", lambda name: None)
    assert ripgrep.rg_available() is False


# --- argv construction (shell=False, pattern/glob as separate elements) -------


class _FakeCompleted:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def _capture_run(monkeypatch, stdout: str = "", returncode: int = 0):
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _FakeCompleted(stdout, returncode)

    monkeypatch.setattr(ripgrep.subprocess, "run", fake_run)
    return captured


def test_rg_search_argv_pattern_and_glob_are_separate_elements(monkeypatch):
    monkeypatch.setattr(ripgrep, "_resolve_bin", lambda b=None: "/usr/bin/rg")
    captured = _capture_run(monkeypatch, stdout="")
    ripgrep.rg_search("/work", "danger; rm -rf /", glob="*.py")
    argv = captured["argv"]
    kwargs = captured["kwargs"]
    # shell must be False and argv must be a list
    assert kwargs["shell"] is False
    assert isinstance(argv, list)
    # pattern is a discrete argv element following -e (not concatenated/shelled)
    assert "-e" in argv
    assert argv[argv.index("-e") + 1] == "danger; rm -rf /"
    # glob is a discrete argv element following -g
    assert "-g" in argv
    assert argv[argv.index("-g") + 1] == "*.py"
    # always hides .git and searches hidden files
    assert "--hidden" in argv
    git_idx = argv.index("--glob")
    assert argv[git_idx + 1] == "!.git/*"
    # env scrubbed to PATH only
    assert set(kwargs["env"].keys()) == {"PATH"}
    assert "timeout" in kwargs


def test_rg_files_argv_glob_separate_and_safe(monkeypatch):
    monkeypatch.setattr(ripgrep, "_resolve_bin", lambda b=None: "/usr/bin/rg")
    captured = _capture_run(monkeypatch, stdout="")
    ripgrep.rg_files("/work", "*.txt")
    argv = captured["argv"]
    kwargs = captured["kwargs"]
    assert kwargs["shell"] is False
    assert "--files" in argv
    assert "--hidden" in argv
    # both the .git exclusion glob and the user glob are present as separate args
    assert argv.count("--glob") == 2
    assert "!.git/*" in argv
    assert "*.txt" in argv


def test_rg_files_no_glob_omits_user_glob(monkeypatch):
    monkeypatch.setattr(ripgrep, "_resolve_bin", lambda b=None: "/usr/bin/rg")
    captured = _capture_run(monkeypatch, stdout="")
    ripgrep.rg_files("/work", None)
    argv = captured["argv"]
    # only the .git exclusion glob remains
    assert argv.count("--glob") == 1
    assert "!.git/*" in argv


# --- JSON parsing of rg_search ------------------------------------------------


_JSON_FIXTURE = "\n".join(
    [
        json.dumps({"type": "begin", "data": {"path": {"text": "./a.txt"}}}),
        json.dumps(
            {
                "type": "match",
                "data": {
                    "path": {"text": "./a.txt"},
                    "lines": {"text": "foo bar\n"},
                    "line_number": 2,
                },
            }
        ),
        json.dumps(
            {
                "type": "match",
                "data": {
                    "path": {"text": "sub/b.txt"},
                    "lines": {"text": "foo again\n"},
                    "line_number": 7,
                },
            }
        ),
        "this-is-not-json",
        json.dumps({"type": "end", "data": {"path": {"text": "./a.txt"}}}),
        json.dumps({"type": "summary", "data": {}}),
    ]
)


def test_rg_search_parses_json_match_lines(monkeypatch):
    monkeypatch.setattr(ripgrep, "_resolve_bin", lambda b=None: "/usr/bin/rg")
    _capture_run(monkeypatch, stdout=_JSON_FIXTURE)
    matches = ripgrep.rg_search("/work", "foo")
    assert [(m.path, m.line, m.text) for m in matches] == [
        ("a.txt", 2, "foo bar"),
        ("sub/b.txt", 7, "foo again"),
    ]


def test_rg_search_empty_pattern_returns_empty(monkeypatch):
    monkeypatch.setattr(ripgrep, "_resolve_bin", lambda b=None: "/usr/bin/rg")
    captured = _capture_run(monkeypatch, stdout=_JSON_FIXTURE)
    assert ripgrep.rg_search("/work", "") == []
    assert "argv" not in captured  # never invoked rg for empty pattern


# --- missing rg -> empty (fallback signal) ------------------------------------


def test_rg_search_missing_binary_returns_empty(monkeypatch):
    monkeypatch.setattr(ripgrep, "_resolve_bin", lambda b=None: None)
    assert ripgrep.rg_search("/work", "foo") == []


def test_rg_files_missing_binary_returns_empty(monkeypatch):
    monkeypatch.setattr(ripgrep, "_resolve_bin", lambda b=None: None)
    assert ripgrep.rg_files("/work", "*.py") == []


# --- timeout / error -> empty (fail-soft) -------------------------------------


def test_rg_search_timeout_returns_empty(monkeypatch):
    monkeypatch.setattr(ripgrep, "_resolve_bin", lambda b=None: "/usr/bin/rg")

    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=5)

    monkeypatch.setattr(ripgrep.subprocess, "run", fake_run)
    assert ripgrep.rg_search("/work", "foo") == []


def test_rg_search_returncode_2_returns_empty(monkeypatch):
    monkeypatch.setattr(ripgrep, "_resolve_bin", lambda b=None: "/usr/bin/rg")
    _capture_run(monkeypatch, stdout="garbage", returncode=2)
    assert ripgrep.rg_search("/work", "foo") == []


def test_rg_search_returncode_1_no_matches_is_ok(monkeypatch):
    monkeypatch.setattr(ripgrep, "_resolve_bin", lambda b=None: "/usr/bin/rg")
    _capture_run(monkeypatch, stdout="", returncode=1)
    assert ripgrep.rg_search("/work", "foo") == []


# --- real-rg smoke (skipped when rg absent) -----------------------------------


_RG = ripgrep.rg_available()


@pytest.mark.skipif(not _RG, reason="rg not installed")
def test_real_rg_search_and_files(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hello world\nfoo bar\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("foo_python = 1\n", encoding="utf-8")
    files = ripgrep.rg_files(str(tmp_path), None)
    assert "a.txt" in files and "b.py" in files
    matches = ripgrep.rg_search(str(tmp_path), "foo")
    paths = {m.path for m in matches}
    assert "a.txt" in paths and "b.py" in paths
