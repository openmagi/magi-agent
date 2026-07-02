"""Deprecation alias shim (rem2/F4): canonical home is
``magi_agent.engine.providers``.

This module replaces itself in ``sys.modules`` with the canonical module so
old and new import paths yield the SAME module object: public and
underscore-private names, ``monkeypatch.setattr`` targets, and ``is``
identity are all preserved byte-compatibly.

Do not add code here. Removal is tracked by a later ratchet shrink round.
"""

from __future__ import annotations

import sys as _sys

from magi_agent.engine import providers as _canonical

_sys.modules[__name__] = _canonical
