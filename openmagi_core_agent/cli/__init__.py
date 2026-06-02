"""Magi headless CLI foundation (PR-A1).

This package is the headless CLI core for the Magi Python runtime. The CLI is
**default-ON** (Track 18 Stream F PR-F2a); set ``MAGI_CLI_ENABLED=0`` (or
``false`` / ``no`` / ``off``) to disable it. It is intentionally importable
*without* ``textual`` or ``google-adk``: only the pure-pydantic runtime modules
(``openmagi_core_agent.runtime.events`` / ``...runtime.control``) plus the
standard library and ``pydantic`` are used here.

Downstream streams (B/C/D/E/F) import the STABLE interface surface from
``openmagi_core_agent.cli.contracts`` only.
"""

from __future__ import annotations

# No ``__all__`` / star-export: consumers import the fully-qualified path
# (e.g. ``from openmagi_core_agent.cli.contracts import EngineDriver``). The
# submodules are intentionally NOT imported here to keep package import cheap
# and side-effect-free.
