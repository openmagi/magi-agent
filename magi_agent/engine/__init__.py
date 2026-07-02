"""Runtime-neutral engine kernel extracted from ``cli/`` (deep-review N-08).

Hosts the stable engine contracts (and, after rem2/F4-F5, provider
resolution and the engine driver) so that transport/runtime/tools import
DOWNWARD instead of reaching into the CLI surface package. Submodules are
intentionally NOT imported here (cheap, side-effect-free package import,
mirroring ``cli/__init__.py``).
"""

from __future__ import annotations
