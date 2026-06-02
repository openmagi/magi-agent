from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .context import ToolContext
from .manifest import ToolManifest
from .result import ToolResult


ToolArguments = dict[str, object]
ToolHandler = Callable[[ToolArguments, ToolContext], ToolResult | Awaitable[ToolResult]]


@dataclass(frozen=True)
class ToolRegistration:
    manifest: ToolManifest
    handler: ToolHandler | None = None
    enabled: bool = False
    protected: bool = False
