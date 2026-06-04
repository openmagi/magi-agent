"""Ripgrep backend for coding-mode Glob/Grep.

This module provides a thin, dependency-free wrapper around the ``rg`` binary
so that gate5b's full toolhost and the local read-only toolhost can share a
single, safe ripgrep implementation. It is flag-gated by the caller; this
module only knows how to discover and invoke ``rg``.

Safety contract (must hold for every caller):

* ``rg`` is invoked with ``shell=False`` and an explicit ``argv`` list. The
  user-supplied ``pattern`` and ``glob`` are passed as **separate argv
  elements** (after ``-e`` / ``-g``) so there is no shell to interpret them and
  no opportunity for shell injection.
* The environment is scrubbed down to ``PATH`` only.
* Every invocation has a wall-clock timeout; on timeout the child is killed and
  an empty result is returned (fail-soft -- callers fall back to Python).
* ``--hidden --glob '!.git/*'`` is always passed so ``.git`` internals are
  never searched. Callers MUST still re-apply their own sealed/secret path
  policy to the returned paths -- ripgrep can see hidden files this module does
  not filter on (only ``.git`` is excluded here).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass

_DEFAULT_RG_BIN = "rg"
_RIPGREP_BIN_ENV = "MAGI_RIPGREP_BIN"
_DEFAULT_TIMEOUT_S = 5.0
# We over-fetch beyond the caller cap so the caller can stat + mtime-sort and
# still end up with a full window of the most-recently-modified results.
_OVERFETCH_MULTIPLIER = 4
_OVERFETCH_FLOOR = 200


@dataclass(frozen=True)
class RgMatch:
    """A single ripgrep match line, normalized to workspace-relative path."""

    path: str
    line: int
    text: str


def _resolve_bin(bin_path: str | None) -> str | None:
    """Resolve the ripgrep binary, honoring MAGI_RIPGREP_BIN then PATH."""

    candidate = bin_path or os.environ.get(_RIPGREP_BIN_ENV) or _DEFAULT_RG_BIN
    # If an explicit absolute/relative path was given, accept it only when it is
    # an executable file; otherwise fall through to PATH lookup by name.
    resolved = shutil.which(candidate)
    return resolved


def rg_available(bin_path: str | None = None) -> bool:
    """Return True when a usable ``rg`` binary can be located.

    Honors the ``MAGI_RIPGREP_BIN`` override and ``bin_path`` argument before
    falling back to ``PATH``. Uses ``shutil.which`` so a configured-but-missing
    binary reports unavailable (callers then use the Python fallback).
    """

    return _resolve_bin(bin_path) is not None


def _run(
    argv: list[str],
    *,
    cwd: str,
    timeout_s: float,
) -> str | None:
    """Run ripgrep safely; return stdout or None on any failure/timeout."""

    try:
        completed = subprocess.run(  # noqa: S603 - argv list, shell=False, scrubbed env
            argv,
            cwd=cwd,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None
    # rg exit code 1 == "no matches" (still valid, empty stdout). 2 == error.
    if completed.returncode not in (0, 1):
        return None
    return completed.stdout


def _normalize_path(raw: str) -> str:
    text = raw.replace("\\", "/")
    if text.startswith("./"):
        text = text[2:]
    return text


def rg_files(
    cwd: str,
    glob: str | None = None,
    *,
    limit: int = 100,
    bin_path: str | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> list[str]:
    """List files under ``cwd`` via ``rg --files`` (workspace-relative paths).

    ``glob`` (when provided) is passed as a separate ``--glob <glob>`` argv
    element. The returned list is uncapped here beyond an over-fetch ceiling;
    the caller is responsible for mtime-sorting and trimming to its final cap.
    """

    resolved = _resolve_bin(bin_path)
    if resolved is None:
        return []
    argv = [resolved, "--files", "--hidden", "--glob", "!.git/*"]
    if glob:
        argv.extend(["--glob", glob])
    argv.append(".")
    stdout = _run(argv, cwd=cwd, timeout_s=timeout_s)
    if stdout is None:
        return []
    ceiling = max(limit * _OVERFETCH_MULTIPLIER, _OVERFETCH_FLOOR)
    files: list[str] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        files.append(_normalize_path(line))
        if len(files) >= ceiling:
            break
    return files


def rg_search(
    cwd: str,
    pattern: str,
    glob: str | None = None,
    *,
    limit: int = 100,
    bin_path: str | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> list[RgMatch]:
    """Regex-search under ``cwd`` via ``rg --json`` (workspace-relative paths).

    ``pattern`` is passed as ``-e <pattern>`` and ``glob`` as ``-g <glob>``,
    each as a separate argv element (no shell). JSON match lines are parsed into
    :class:`RgMatch`. Over-fetches beyond ``limit`` so the caller can
    mtime-sort and trim; malformed JSON lines are skipped.
    """

    resolved = _resolve_bin(bin_path)
    if resolved is None or not pattern:
        return []
    argv = [resolved, "--json", "--hidden", "--glob", "!.git/*"]
    if glob:
        argv.extend(["-g", glob])
    argv.extend(["-e", pattern, "."])
    stdout = _run(argv, cwd=cwd, timeout_s=timeout_s)
    if stdout is None:
        return []
    ceiling = max(limit * _OVERFETCH_MULTIPLIER, _OVERFETCH_FLOOR)
    matches: list[RgMatch] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict) or event.get("type") != "match":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        path_obj = data.get("path")
        path_text = (
            path_obj.get("text") if isinstance(path_obj, dict) else None
        )
        if not isinstance(path_text, str):
            continue
        lines_obj = data.get("lines")
        line_text = (
            lines_obj.get("text") if isinstance(lines_obj, dict) else ""
        )
        if not isinstance(line_text, str):
            line_text = ""
        line_number = data.get("line_number")
        if not isinstance(line_number, int):
            line_number = 0
        matches.append(
            RgMatch(
                path=_normalize_path(path_text),
                line=line_number,
                text=line_text.rstrip("\n"),
            )
        )
        if len(matches) >= ceiling:
            break
    return matches
