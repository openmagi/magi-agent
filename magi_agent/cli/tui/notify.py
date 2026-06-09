"""Toast helpers for the Magi TUI (PR3.3).

Thin, fail-safe wrappers over ``App.notify`` so call sites that previously
swallowed errors silently (``except: pass``) surface a visible, severity-tagged
toast instead. Every helper is fail-safe: a notification that itself raises is
swallowed (a failed toast must never crash a turn).

The three severities mirror Textual's ``App.notify`` severity strings:
``information`` / ``warning`` / ``error``.

> The focus-aware bell / desktop-notify surface (``BELL_ENV`` / ``bell_enabled``
> / ``notify_attention``) is gated and lands with PR3.4; ``__all__`` is extended
> there so this module's public surface grows additively without churning PR3.3.
"""

from __future__ import annotations

__all__ = ["info", "warning", "error"]


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
