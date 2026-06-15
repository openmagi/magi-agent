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
This is a *file-driven* overlay, mirroring :mod:`cli.memory_bootstrap`. With NO
profile file present it is a no-op, so the repo's code-level gate defaults (and
a plain ``pip install`` with no seeded file) are unchanged. The packaging layer
that wants install-default-on writes the file; the runtime code stays honest.

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


def apply_install_profile_bootstrap(
    environ: MutableMapping[str, str],
    *,
    profile_path: Path | str | None = None,
) -> None:
    """Overlay ``profile.env`` flags into ``environ`` via ``setdefault``.

    Args:
        environ: The process env to mutate (normally ``os.environ``).
        profile_path: Path to the profile file; defaults to
            ``~/.magi/profile.env``. Injectable for tests.
    """
    path = Path(profile_path) if profile_path is not None else DEFAULT_PROFILE_PATH
    try:
        if not path.is_file():
            return
        text = path.read_text(encoding="utf-8")
    except OSError:
        logger.debug("install profile: cannot read %s; skipping", path, exc_info=True)
        return
    except Exception:  # pragma: no cover - defensive; keep startup alive
        logger.debug("install profile: unexpected load failure; skipping", exc_info=True)
        return

    for raw_line in text.splitlines():
        try:
            parsed = _parse_line(raw_line)
            if parsed is None:
                continue
            key, value = parsed
            environ.setdefault(key, value)
        except Exception:  # pragma: no cover - defensive; per-line tolerance
            logger.debug("install profile: skipping malformed line", exc_info=True)


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


__all__ = ["apply_install_profile_bootstrap", "DEFAULT_PROFILE_PATH"]
