from __future__ import annotations

import json
import os
import subprocess
import time
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


# --- _ripgrep_glob_arg sentinel cases (None = no --glob filter) ---------------


def test_ripgrep_glob_arg_double_star_returns_none():
    """'**/*' means all files — rg should be called without a --glob filter."""
    from magi_agent.gates.gate5b_full_toolhost import _ripgrep_glob_arg

    assert _ripgrep_glob_arg("**/*") is None


def test_ripgrep_glob_arg_single_star_returns_none():
    """'*' means all files — rg should be called without a --glob filter."""
    from magi_agent.gates.gate5b_full_toolhost import _ripgrep_glob_arg

    assert _ripgrep_glob_arg("*") is None


# --- mtime_sort shared helper -------------------------------------------------


def test_mtime_sort_basic_descending(tmp_path: Path):
    """Items are returned sorted newest-first."""
    now = time.time()
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    c = tmp_path / "c.txt"
    a.write_text("a")
    b.write_text("b")
    c.write_text("c")
    os.utime(a, (now - 100, now - 100))
    os.utime(b, (now, now))
    os.utime(c, (now - 50, now - 50))
    result = ripgrep.mtime_sort(
        [str(a), str(b), str(c)],
        stat_path=lambda p: p,
        limit=10,
    )
    assert result == [str(b), str(c), str(a)]


def test_mtime_sort_limit(tmp_path: Path):
    """limit trims the result list."""
    now = time.time()
    files = []
    for i in range(5):
        f = tmp_path / f"{i}.txt"
        f.write_text(str(i))
        os.utime(f, (now - i, now - i))
        files.append(str(f))
    result = ripgrep.mtime_sort(files, stat_path=lambda p: p, limit=3)
    assert len(result) == 3
    # newest-first order — file 0 was touched last (now-0)
    assert result[0] == str(tmp_path / "0.txt")


def test_mtime_sort_stat_failure_skips_item(tmp_path: Path):
    """Items whose path raises OSError are silently dropped."""
    now = time.time()
    real = tmp_path / "real.txt"
    real.write_text("x")
    os.utime(real, (now, now))
    missing = str(tmp_path / "nonexistent.txt")
    result = ripgrep.mtime_sort(
        [missing, str(real)],
        stat_path=lambda p: p,
        limit=10,
    )
    assert result == [str(real)]


def test_mtime_sort_tiebreak_by_path(tmp_path: Path):
    """Equal-mtime items are broken deterministically by path string."""
    fixed_time = 1_700_000_000.0
    a = tmp_path / "aaa.txt"
    b = tmp_path / "bbb.txt"
    a.write_text("a")
    b.write_text("b")
    os.utime(a, (fixed_time, fixed_time))
    os.utime(b, (fixed_time, fixed_time))
    result = ripgrep.mtime_sort(
        [str(b), str(a)],
        stat_path=lambda p: p,
        limit=10,
    )
    # lexicographically smaller path wins (sorted ascending as tiebreak)
    assert result[0] == str(a)
    assert result[1] == str(b)


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
