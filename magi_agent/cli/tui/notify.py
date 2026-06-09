"""Toast + bell helpers for the Magi TUI (PR3.3 + PR3.4).

Two concerns:

* **Toasts** (PR3.3) — thin, fail-safe wrappers over ``App.notify`` so call
  sites that previously swallowed errors silently (``except: pass``) surface a
  visible, severity-tagged toast instead. Every helper is fail-safe: a
  notification that itself raises is swallowed (a failed toast must never crash
  a turn). The three severities mirror Textual's ``App.notify`` severity
  strings: ``information`` / ``warning`` / ``error``.
* **Focus-aware bell** (PR3.4) — ring the terminal bell (``App.bell()``) when
  the terminal is UNFOCUSED, gated by the ``MAGI_TUI_NOTIFY_BELL`` env flag
  (default OFF). The bell never fires while focused (the operator is already
  looking) and is fail-open like the toast helpers. ``App.bell()`` routes a
  portable BEL through the Textual driver safely.

Deferred follow-up: a true desktop notification should go through a proper
Textual driver/app seam (NOT a raw ``sys.stdout`` OSC write, which can corrupt
a live frame or leak visible escape bytes on a piped stdout).
"""

from __future__ import annotations

import os

__all__ = [
    "info",
    "warning",
    "error",
    "BELL_ENV",
    "bell_enabled",
    "notify_attention",
]


def info(app: object, message: str, *, timeout: float = 4.0) -> None:
    """Surface an informational toast (severity ``information``)."""

    _notify(app, message, severity="information", timeout=timeout)


def warning(app: object, message: str, *, timeout: float = 6.0) -> None:
    """Surface a warning toast (severity ``warning``)."""

    _notify(app, message, severity="warning", timeout=timeout)


def error(app: object, message: str, *, timeout: float = 8.0) -> None:
    """Surface an error toast (severity ``error``)."""

    _notify(app, message, severity="error", timeout=timeout)


def _notify(app: object, message: str, *, severity: str, timeout: float) -> None:
    notify = getattr(app, "notify", None)
    if not callable(notify):
        return
    try:
        notify(message, severity=severity, timeout=timeout)
    except Exception:
        # A toast that fails must never crash a turn (fail-open). This is a
        # genuinely benign swallow: the message was already best-effort and the
        # alternative (propagating) would take down the engine turn.
        pass


# ---------------------------------------------------------------------------
# Focus-aware bell (PR3.4) — gated by MAGI_TUI_NOTIFY_BELL.
# ---------------------------------------------------------------------------

#: Env flag gating the focus-aware terminal bell / desktop notify (default OFF).
BELL_ENV = "MAGI_TUI_NOTIFY_BELL"

# Accepted truthy spellings for the gate; anything else (incl. unset) -> OFF.
_TRUTHY = {"1", "true", "yes", "on"}


def bell_enabled() -> bool:
    """True iff ``MAGI_TUI_NOTIFY_BELL`` is set to a truthy value (default OFF).

    Unset, empty, or any non-truthy value (e.g. ``"0"``/``"false"``) -> ``False``
    so the bell is strictly opt-in.
    """

    return os.environ.get(BELL_ENV, "").strip().lower() in _TRUTHY


def notify_attention(app: object, *, focused: bool, reason: str = "") -> None:
    """Ring the terminal bell when enabled AND the terminal is unfocused.

    No-op when the gate is off (default) OR the terminal is focused (the operator
    is already looking — don't annoy). A missing/raising ``bell`` is swallowed —
    an attention cue must never crash a turn. ``reason`` is accepted for call-site
    parity but currently unused (a real desktop notification is a deferred
    follow-up; see module docstring).
    """

    if focused or not bell_enabled():
        return
    bell = getattr(app, "bell", None)
    if callable(bell):
        try:
            bell()
        except Exception:
            # A bell that fails must never crash a turn (fail-open).
            pass
