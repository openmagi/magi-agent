"""Format-after-edit selection and a thin, fail-open formatter runner.

OpenCode runs a formatter after every successful file write and then re-reads
the formatted file so the next edit's ``old_string`` matches the formatted
state (``format/index.ts``). This module ports the *selection* logic as a pure,
data-driven mapping plus a small subprocess runner that:

* picks a formatter command for a file extension,
* checks availability with ``shutil.which``,
* runs it with a scrubbed env, ``shell=False`` argv list, and a short timeout,
* never raises on a missing/failing/timed-out formatter (fail-open).

The mapping is intentionally a small data structure so it is easy to extend,
and it can be overridden via the ``MAGI_FORMATTER_OVERRIDES`` env var
(``ext=cmd`` CSV, e.g. ``.py=ruff format $FILE,.js=prettier --write $FILE``).

.. note:: ``MAGI_FORMATTER_OVERRIDES`` uses ``','`` as the entry delimiter.
   A file-extension path or command that itself contains a literal comma will
   break parsing.  Use a wrapper script when the formatter command requires
   comma-containing arguments.
"""
from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# The ``$FILE`` placeholder is replaced with the resolved absolute path as a
# single argv element (never shell-interpolated).
FILE_PLACEHOLDER = "$FILE"

# Default extension -> formatter command template (argv tokens, with $FILE).
# Easy to extend: add another ``".ext": "cmd ... $FILE"`` entry.
DEFAULT_FORMATTERS: dict[str, str] = {
    ".py": "ruff format $FILE",
    ".pyi": "ruff format $FILE",
    ".js": "prettier --write $FILE",
    ".jsx": "prettier --write $FILE",
    ".ts": "prettier --write $FILE",
    ".tsx": "prettier --write $FILE",
    ".mjs": "prettier --write $FILE",
    ".cjs": "prettier --write $FILE",
    ".json": "prettier --write $FILE",
    ".md": "prettier --write $FILE",
    ".css": "prettier --write $FILE",
    ".scss": "prettier --write $FILE",
    ".html": "prettier --write $FILE",
    ".yaml": "prettier --write $FILE",
    ".yml": "prettier --write $FILE",
    ".go": "gofmt -w $FILE",
    ".rs": "rustfmt $FILE",
    ".sh": "shfmt -w $FILE",
}

_OVERRIDES_ENV = "MAGI_FORMATTER_OVERRIDES"


@dataclass(frozen=True)
class FormatterSelection:
    """A resolved formatter command for a specific file."""

    extension: str
    program: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class FormatterRunResult:
    """Outcome of attempting to format a file. Always fail-open."""

    attempted: bool
    formatted: bool
    program: str | None = None
    exit_code: int | None = None
    reason: str = ""


def parse_formatter_overrides(raw: str | None) -> dict[str, str]:
    """Parse ``MAGI_FORMATTER_OVERRIDES`` (``ext=cmd`` CSV) into a mapping.

    Invalid entries (missing ``=`` or empty parts) are skipped. Extensions are
    normalized to lower-case and given a leading dot.

    .. warning:: Entries are split on ``','``.  A formatter command or
       extension that contains a literal comma will break parsing.  Use a
       wrapper script if the formatter requires comma-containing arguments.
    """
    overrides: dict[str, str] = {}
    if not raw:
        return overrides
    for entry in raw.split(","):
        token = entry.strip()
        if not token or "=" not in token:
            continue
        ext_raw, _, cmd_raw = token.partition("=")
        ext = ext_raw.strip().lower()
        cmd = cmd_raw.strip()
        if not ext or not cmd:
            continue
        if not ext.startswith("."):
            ext = "." + ext
        overrides[ext] = cmd
    return overrides


def build_formatter_table(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the default formatter table merged with env overrides."""
    table = dict(DEFAULT_FORMATTERS)
    source = env if env is not None else os.environ
    table.update(parse_formatter_overrides(source.get(_OVERRIDES_ENV)))
    return table


def select_formatter(
    file_path: str | os.PathLike[str],
    *,
    env: Mapping[str, str] | None = None,
    which: object | None = None,
) -> FormatterSelection | None:
    """Select an available formatter for ``file_path``, or ``None``.

    Returns ``None`` when the extension is unmapped or the program is not
    installed (``shutil.which`` returns falsy). ``which`` is injectable for
    deterministic tests.
    """
    resolver = which if which is not None else shutil.which
    path_str = os.fspath(file_path)
    ext = Path(path_str).suffix.lower()
    if not ext:
        return None
    table = build_formatter_table(env)
    template = table.get(ext)
    if not template:
        return None
    try:
        tokens = shlex.split(template)
    except ValueError:
        logger.warning(
            "MAGI_FORMATTER_OVERRIDES: malformed shell quoting in template for %r"
            " — skipping formatter (no formatter selected)",
            ext,
        )
        return None
    if not tokens:
        return None
    program = tokens[0]
    if not resolver(program):
        return None
    argv = tuple(path_str if token == FILE_PLACEHOLDER else token for token in tokens)
    # If the template never referenced $FILE, append the path so the formatter
    # actually targets the written file.
    if FILE_PLACEHOLDER not in tokens:
        argv = (*argv, path_str)
    return FormatterSelection(extension=ext, program=program, argv=argv)


def run_formatter(
    file_path: str | os.PathLike[str],
    *,
    timeout_seconds: float,
    env: Mapping[str, str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
    which: object | None = None,
) -> FormatterRunResult:
    """Run the selected formatter on ``file_path``. Always fail-open.

    Subprocess safety: ``shell=False`` with an argv list, a scrubbed env
    (PATH only), ``capture_output`` (formatter stdout/stderr never surfaced),
    and a short timeout. A missing/failing/timed-out formatter never raises;
    the (already-written) file is left untouched and ``formatted=False``.
    """
    selection = select_formatter(file_path, env=env, which=which)
    if selection is None:
        return FormatterRunResult(attempted=False, formatted=False, reason="no_formatter")
    scrubbed_env = {"PATH": (env or os.environ).get("PATH", "/usr/bin:/bin")}
    try:
        completed = subprocess.run(  # noqa: S603 - argv list, shell=False, scrubbed env
            list(selection.argv),
            cwd=cwd,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=scrubbed_env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return FormatterRunResult(
            attempted=True,
            formatted=False,
            program=selection.program,
            reason="timeout",
        )
    except OSError:
        return FormatterRunResult(
            attempted=True,
            formatted=False,
            program=selection.program,
            reason="os_error",
        )
    if completed.returncode != 0:
        return FormatterRunResult(
            attempted=True,
            formatted=False,
            program=selection.program,
            exit_code=completed.returncode,
            reason="nonzero_exit",
        )
    return FormatterRunResult(
        attempted=True,
        formatted=True,
        program=selection.program,
        exit_code=0,
        reason="ok",
    )


__all__ = [
    "DEFAULT_FORMATTERS",
    "FILE_PLACEHOLDER",
    "FormatterRunResult",
    "FormatterSelection",
    "build_formatter_table",
    "parse_formatter_overrides",
    "run_formatter",
    "select_formatter",
]
