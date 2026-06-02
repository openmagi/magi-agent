"""Command registry package for the Magi CLI (Stream D).

Public surface imported by Streams D2 / E / F. Importing this package is cheap
and side-effect-free: it does NOT build any registry, touch an event loop, or
run command discovery (per-cwd registries are built lazily by
``get_registry``).
"""

from __future__ import annotations

from openmagi_core_agent.cli.commands.builtins import builtin_commands
from openmagi_core_agent.cli.commands.discovery import (
    build_registry,
    discover_commands,
    install_discovery,
    markdown_commands,
)
from openmagi_core_agent.cli.commands.registry import (
    CommandRegistryImpl,
    dispatch,
    get_registry,
    set_registry_builder,
)

__all__ = [
    "CommandRegistryImpl",
    "get_registry",
    "set_registry_builder",
    "dispatch",
    # PR-D2: discovery + builtins public surface
    "builtin_commands",
    "discover_commands",
    "markdown_commands",
    "build_registry",
    "install_discovery",
]
