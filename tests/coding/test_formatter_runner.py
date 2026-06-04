"""PR4: format-after-edit formatter selection + fail-open runner tests."""
from __future__ import annotations

import os
import stat
import sys

from magi_agent.coding.formatter_runner import (
    DEFAULT_FORMATTERS,
    FILE_PLACEHOLDER,
    build_formatter_table,
    parse_formatter_overrides,
    run_formatter,
    select_formatter,
)


def _which_all(_program: str) -> str:
    return "/usr/bin/fake"


def _which_none(_program: str) -> None:
    return None


def test_extension_mapping_covers_expected_languages() -> None:
    assert DEFAULT_FORMATTERS[".py"].split()[0] == "ruff"
    assert DEFAULT_FORMATTERS[".ts"].split()[0] == "prettier"
    assert DEFAULT_FORMATTERS[".go"].split()[0] == "gofmt"
    assert DEFAULT_FORMATTERS[".rs"].split()[0] == "rustfmt"
    assert DEFAULT_FORMATTERS[".sh"].split()[0] == "shfmt"


def test_select_formatter_resolves_argv_with_file_placeholder() -> None:
    selection = select_formatter("pkg/module.py", env={}, which=_which_all)
    assert selection is not None
    assert selection.extension == ".py"
    assert selection.program == "ruff"
    assert selection.argv == ("ruff", "format", "pkg/module.py")
    # $FILE was substituted as a single argv element, not shell-interpolated.
    assert FILE_PLACEHOLDER not in selection.argv


def test_select_formatter_unmapped_extension_returns_none() -> None:
    assert select_formatter("data.bin", env={}, which=_which_all) is None
    assert select_formatter("README", env={}, which=_which_all) is None


def test_select_formatter_missing_program_returns_none() -> None:
    # ruff/prettier "not installed" -> which returns None -> no selection.
    assert select_formatter("module.py", env={}, which=_which_none) is None


def test_override_parsing_csv() -> None:
    parsed = parse_formatter_overrides(".py=myfmt $FILE, txt=other --fix $FILE , bad")
    assert parsed[".py"] == "myfmt $FILE"
    # leading dot is added automatically.
    assert parsed[".txt"] == "other --fix $FILE"
    # malformed entry (no '=') is skipped.
    assert "bad" not in parsed


def test_override_parsing_empty_or_none() -> None:
    assert parse_formatter_overrides(None) == {}
    assert parse_formatter_overrides("") == {}
    assert parse_formatter_overrides("   ,  , =cmd, ext=") == {}


def test_build_table_merges_env_override() -> None:
    table = build_formatter_table({"MAGI_FORMATTER_OVERRIDES": ".py=myfmt $FILE"})
    assert table[".py"] == "myfmt $FILE"
    # non-overridden defaults remain.
    assert table[".ts"] == DEFAULT_FORMATTERS[".ts"]


def test_override_selects_custom_command() -> None:
    selection = select_formatter(
        "module.py",
        env={"MAGI_FORMATTER_OVERRIDES": ".py=myfmt --quiet $FILE"},
        which=_which_all,
    )
    assert selection is not None
    assert selection.program == "myfmt"
    assert selection.argv == ("myfmt", "--quiet", "module.py")


def test_run_formatter_no_formatter_is_fail_open(tmp_path) -> None:
    target = tmp_path / "data.bin"
    target.write_text("noop", encoding="utf-8")
    result = run_formatter(target, timeout_seconds=2.0, env={}, which=_which_all)
    assert result.attempted is False
    assert result.formatted is False
    assert result.reason == "no_formatter"


def test_run_formatter_real_subprocess_formats_via_override(tmp_path) -> None:
    # A tiny deterministic "formatter" script that rewrites the file.
    script = tmp_path / "fakefmt.py"
    script.write_text(
        "import sys\n"
        "p = sys.argv[1]\n"
        "open(p, 'w', encoding='utf-8').write('FORMATTED\\n')\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    target = tmp_path / "module.py"
    target.write_text("x=1\n", encoding="utf-8")
    overrides = f".py={sys.executable} {script} $FILE"
    result = run_formatter(
        target,
        timeout_seconds=10.0,
        env={"MAGI_FORMATTER_OVERRIDES": overrides, "PATH": os.environ.get("PATH", "")},
    )
    assert result.attempted is True
    assert result.formatted is True
    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == "FORMATTED\n"


def test_run_formatter_nonzero_exit_is_fail_open(tmp_path) -> None:
    script = tmp_path / "failfmt.py"
    script.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
    target = tmp_path / "module.py"
    target.write_text("x=1\n", encoding="utf-8")
    overrides = f".py={sys.executable} {script} $FILE"
    result = run_formatter(
        target,
        timeout_seconds=10.0,
        env={"MAGI_FORMATTER_OVERRIDES": overrides, "PATH": os.environ.get("PATH", "")},
    )
    assert result.attempted is True
    assert result.formatted is False
    assert result.exit_code == 3
    assert result.reason == "nonzero_exit"
    # File untouched.
    assert target.read_text(encoding="utf-8") == "x=1\n"


def test_select_formatter_malformed_override_returns_none(monkeypatch) -> None:
    """Unmatched shell quote in MAGI_FORMATTER_OVERRIDES → None (no formatter)."""
    # Inject a broken template (unmatched single quote) directly into the table
    # by using the env dict path so we don't rely on a real shell quoting error
    # leaking through parse_formatter_overrides (which doesn't shlex-split).
    # We override the table entry directly by injecting via env.
    malformed_env = {"MAGI_FORMATTER_OVERRIDES": ".py=myfmt 'unclosed $FILE"}
    result = select_formatter("module.py", env=malformed_env, which=_which_all)
    # shlex.split raises ValueError on the unmatched quote → select_formatter
    # must catch it and return None instead of propagating.
    assert result is None


def test_run_formatter_malformed_override_write_succeeds(tmp_path, monkeypatch) -> None:
    """Malformed override → no formatter → write is still fail-open (no exception)."""
    target = tmp_path / "module.py"
    target.write_text("x=1\n", encoding="utf-8")
    result = run_formatter(
        target,
        timeout_seconds=2.0,
        env={"MAGI_FORMATTER_OVERRIDES": ".py=myfmt 'unclosed $FILE"},
        which=_which_all,
    )
    assert result.attempted is False
    assert result.formatted is False
    assert result.reason == "no_formatter"
    # File is untouched — write-side is unaffected.
    assert target.read_text(encoding="utf-8") == "x=1\n"


def test_select_formatter_env_none_uses_os_environ(monkeypatch) -> None:
    """env=None falls back to os.environ; MAGI_FORMATTER_OVERRIDES is honoured."""
    monkeypatch.setenv("MAGI_FORMATTER_OVERRIDES", ".py=myfmt --quiet $FILE")
    # env=None (default) → build_formatter_table reads os.environ.
    result = select_formatter("module.py", which=_which_all)
    assert result is not None
    assert result.program == "myfmt"
    assert result.argv == ("myfmt", "--quiet", "module.py")


def test_run_formatter_env_none_uses_os_environ(tmp_path, monkeypatch) -> None:
    """run_formatter with env=None picks up MAGI_FORMATTER_OVERRIDES from os.environ."""
    import sys

    script = tmp_path / "fakefmt.py"
    script.write_text(
        "import sys\n"
        "p = sys.argv[1]\n"
        "open(p, 'w', encoding='utf-8').write('FROM_OS_ENV\\n')\n",
        encoding="utf-8",
    )
    target = tmp_path / "module.py"
    target.write_text("x=1\n", encoding="utf-8")
    overrides = f".py={sys.executable} {script} $FILE"
    monkeypatch.setenv("MAGI_FORMATTER_OVERRIDES", overrides)
    # env=None → os.environ → formatter override is found.
    result = run_formatter(target, timeout_seconds=10.0)
    assert result.attempted is True
    assert result.formatted is True
    assert target.read_text(encoding="utf-8") == "FROM_OS_ENV\n"


def test_run_formatter_timeout_is_fail_open(tmp_path) -> None:
    script = tmp_path / "slowfmt.py"
    script.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
    target = tmp_path / "module.py"
    target.write_text("x=1\n", encoding="utf-8")
    overrides = f".py={sys.executable} {script} $FILE"
    result = run_formatter(
        target,
        timeout_seconds=0.5,
        env={"MAGI_FORMATTER_OVERRIDES": overrides, "PATH": os.environ.get("PATH", "")},
    )
    assert result.attempted is True
    assert result.formatted is False
    assert result.reason == "timeout"
