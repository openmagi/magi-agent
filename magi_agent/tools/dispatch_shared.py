"""Shared helpers for the two live tool-dispatch kernels.

``ToolDispatcher`` (``tools/dispatcher.py``) and ``ToolExecutionKernel``
(``tools/kernel.py``) are deliberately separate dispatch boundaries with
distinct consumers; this module holds only the helpers they would otherwise
duplicate byte-for-byte. No policy decisions (permission, gating,
sanitization) live here — policy stays in the kernel that owns it.
"""
from __future__ import annotations

from .manifest import RuntimeMode
from .registry import ToolRegistry


def _available_tool_names(
    registry: ToolRegistry,
    exposed_tool_names: tuple[str, ...] | None,
    *,
    mode: RuntimeMode,
) -> tuple[str, ...]:
    if exposed_tool_names is not None:
        return tuple(sorted(dict.fromkeys(exposed_tool_names)))
    return tuple(tool.name for tool in registry.list_available(mode=mode))
