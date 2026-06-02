"""Interactive Textual TUI for the Magi headless CLI (Stream E).

``textual`` / ``rich`` are imported ONLY inside this subpackage (and the future
``cli/render`` + ``cli/keybindings`` packages). Importing
``magi_agent.cli.tui`` is allowed to pull ``textual`` (the transcript
module needs it), but no module OUTSIDE these subpackages may name ``textual`` or
``rich`` at module load — that import-cleanliness invariant is guarded by the
engine tests.
"""

from __future__ import annotations
