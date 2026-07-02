"""Deprecation alias shim (rem2/F6): canonical home is
``magi_agent.shared.tool_preview``.

``tool_preview`` (secret-token redaction + preview capping) is a pure ``re``
leaf, so it belongs in ``shared`` rather than ``transport``. This module
replaces itself in ``sys.modules`` with the canonical module so old and new
import paths yield the SAME module object.

Do not add code here. Removal is tracked by a later ratchet shrink round.
"""

from __future__ import annotations

import sys as _sys

from magi_agent.shared import tool_preview as _canonical

_sys.modules[__name__] = _canonical
