"""CLI install profile bootstrap: ``~/.magi/profile.env`` → process env.

WHY THIS EXISTS
---------------
Every runtime feature gate reads the **process ENV** (``MAGI_*``). A fresh
install therefore starts with all opt-in gates OFF unless the operator manually
exports them every launch. This module bridges that gap: at real CLI startup it
loads a ``KEY=VAL`` profile file and ``setdefault``s each matching env var, so a
packaged install (e.g. Homebrew seeds ``~/.magi/profile.env``) boots with its
chosen profile already on.

INSTALL-DEFAULT, CODE-DEFAULT-OFF
---------------------------------
This is primarily a *file-driven* overlay, mirroring :mod:`cli.memory_bootstrap`.
A small list of "install-default" gates (see ``EMBEDDED_DEFAULT_PROFILE``) also
ships as an *embedded* fallback so a fresh ``pip install`` boots with live
subagents on out of the box — without that, ``magi serve`` looks broken because
the dashboard Work pane shows no subagents. The runtime-code gate defaults
remain OFF; this loader is what flips them on at CLI startup. Operators that
want a gate truly off can either ``export MAGI_*=0`` before launching or write
a ``profile.env`` line that overrides the embedded value.

PRECEDENCE + SAFETY
-------------------
* Each key is applied via ``os.environ.setdefault`` so an explicit pre-set env
  var STILL WINS. Precedence: ``env > profile file``.
* Only ``MAGI_`` keys are honoured — the profile can never inject
  ``PATH``/``HOME``/etc.
* Fail-soft: an unreadable/malformed file (or line) never crashes startup; valid
  lines are still applied.

This loader runs from the real CLI entrypoints (``main:main`` / ``cli.app:main``)
under the same runtime-profile gate as the memory bootstrap, so an explicit
``MAGI_RUNTIME_PROFILE=safe|eval`` (lean/measurement) skips it entirely.
"""
from __future__ import annotations

import logging
import os
from collections.abc import MutableMapping
from pathlib import Path

logger = logging.getLogger(__name__)

#: Default location of the install profile, seeded by the packaging layer.
DEFAULT_PROFILE_PATH = Path.home() / ".magi" / "profile.env"

#: Only keys with these prefixes are applied (the profile cannot set arbitrary
#: process env such as ``PATH``/``HOME``).
_ALLOWED_PREFIXES = ("MAGI_",)

#: Embedded fallback profile applied when ``~/.magi/profile.env`` does NOT
#: provide the key. Mirrors the install profile the Homebrew tap seeds for a
#: working out-of-the-box ``magi serve`` (live subagents → Work-pane visibility
#: in the local dashboard). Every key still respects user precedence:
#:
#:    explicit env > profile.env file entry > embedded default
#:
#: To OPT OUT, either set the env var explicitly to ``0``/``false``, or write
#: a ``profile.env`` line that overrides the value.
EMBEDDED_DEFAULT_PROFILE: dict[str, str] = {
    "MAGI_CHILD_RUNNER_LIVE_ENABLED": "1",
    "MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED": "1",
}


def apply_install_profile_bootstrap(
    environ: MutableMapping[str, str],
    *,
    profile_path: Path | str | None = None,
) -> None:
    """Overlay ``profile.env`` flags into ``environ`` via ``setdefault``.

    Precedence (highest wins): explicit env → profile.env file → embedded
    default. The embedded default lets a plain ``pip install`` (no Homebrew
    seed) still boot with the install-default flags on, so a fresh install
    works out of the box without manual ``export`` lines.

    Args:
        environ: The process env to mutate (normally ``os.environ``).
        profile_path: Path to the profile file; defaults to
            ``~/.magi/profile.env``. Injectable for tests.
    """
    path = Path(profile_path) if profile_path is not None else DEFAULT_PROFILE_PATH

    # File overlay — fail-soft. If the file is absent/unreadable/malformed we
    # still fall through to the embedded defaults below.
    try:
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            for raw_line in text.splitlines():
                try:
                    parsed = _parse_line(raw_line)
                    if parsed is None:
                        continue
                    key, value = parsed
                    environ.setdefault(key, value)
                except Exception:  # pragma: no cover - per-line tolerance
                    logger.debug(
                        "install profile: skipping malformed line", exc_info=True,
                    )
    except OSError:
        logger.debug("install profile: cannot read %s; skipping", path, exc_info=True)
    except Exception:  # pragma: no cover - defensive; keep startup alive
        logger.debug("install profile: unexpected load failure; skipping", exc_info=True)

    # Embedded defaults — applied via ``setdefault`` so explicit env and any
    # profile.env entry still win over them.
    for key, value in EMBEDDED_DEFAULT_PROFILE.items():
        environ.setdefault(key, value)


def _parse_line(raw_line: str) -> tuple[str, str] | None:
    """Return ``(key, value)`` for an allowed ``KEY=VAL`` line, else ``None``."""
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export ") :].strip()
    if "=" not in line:
        return None
    key, _, value = line.partition("=")
    key = key.strip()
    if not key or not key.startswith(_ALLOWED_PREFIXES):
        return None
    return key, _unquote(value.strip())


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


__all__ = [
    "apply_install_profile_bootstrap",
    "DEFAULT_PROFILE_PATH",
    "EMBEDDED_DEFAULT_PROFILE",
]
