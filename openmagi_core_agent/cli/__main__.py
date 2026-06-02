"""Thin stdlib-only shim for ``python -m openmagi_core_agent.cli`` (PR-F1).

Layer-0 fast path: ``--version`` / ``-V`` prints the package version and exits
WITHOUT importing typer, textual, google-adk, or cli.app. Only stdlib modules
(``sys``, ``importlib.metadata``) are used on this path.

All other invocations lazily import ``cli.app.main`` and delegate.

This is the module invoked by ``python -m openmagi_core_agent.cli`` and the
future ``magi`` console-script entry point registered in pyproject.toml.
"""

from __future__ import annotations

import sys

# ---------------------------------------------------------------------------
# Version constant (resolved at import via importlib.metadata; stdlib-only).
# ---------------------------------------------------------------------------

def _get_version() -> str:
    try:
        from importlib.metadata import version  # noqa: PLC0415 - stdlib fast path
        return version("clawy-core-agent-python")
    except Exception:
        return "0.0.0-dev"


_FALLBACK_VERSION = "0.0.0-dev"


def main() -> None:
    """Entry point: stdlib-only --version fast path, then delegate to cli.app."""

    # ---------------------------------------------------------------------- #
    # Layer-0 fast path: --version / -V                                      #
    # Imports: sys, importlib.metadata only. Zero typer / textual / ADK.     #
    # ---------------------------------------------------------------------- #
    if "--version" in sys.argv or "-V" in sys.argv:
        ver = _get_version()
        print(ver)
        sys.exit(0)

    # ---------------------------------------------------------------------- #
    # All other invocations: delegate to the Typer app.                       #
    # Lazy import: cli.app pulls typer; textual is only imported if TUI runs. #
    # ---------------------------------------------------------------------- #
    from openmagi_core_agent.cli.app import main as app_main  # noqa: PLC0415

    app_main()


if __name__ == "__main__":
    main()
