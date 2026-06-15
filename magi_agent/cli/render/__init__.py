"""Surface-specific render helpers for the Magi CLI TUI stream.

This package (and ``cli/tui/``) is the ONLY place ``rich``/``textual`` may be
imported. The diff engine lives in ``diff.py``; per-tool renderers consuming it
live in ``cli/tui/tool_render.py``.
"""

from __future__ import annotations

from magi_agent.cli.render.width import display_width, truncate_cells

__all__: list[str] = ["display_width", "truncate_cells"]
