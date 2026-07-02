"""Deprecation alias shim (rem2/G1): canonical home is
``magi_agent.engine.engine_user_packs``.

This module replaces itself in ``sys.modules`` with the canonical module so
old and new import paths yield the SAME module object. The user-pack gate
helpers were pure-moved out of the engine driver; keeping this alias preserves
``from magi_agent.cli.engine_user_packs import ...`` importers.

Do not add code here.
"""

from __future__ import annotations

import sys as _sys

from magi_agent.engine import engine_user_packs as _canonical

_sys.modules[__name__] = _canonical
