"""First-party read-only local connector provider (no privilege, typed-ctx only).

Receives ONLY the narrow ``ConnectorProvideContext`` (D5) and registers a
``ConnectorSpec`` carrying its projected ``ToolManifest``s. The projector
(``magi_agent/packs/connector_projection.py``) lands those manifests in the live
tool registry — the same mode-scoped offer path native tools use (Group A seam).
"""
from __future__ import annotations

from magi_agent.packs.context import ConnectorProvideContext, ConnectorSpec
from magi_agent.tools.catalog import CORE_TOOL_INPUT_SCHEMA
from magi_agent.tools.manifest import Budget, ToolManifest, ToolSource

_CONNECTOR_SOURCE = ToolSource(kind="external", package="openmagi.connector-local-readonly")


def provide_connector(context: ConnectorProvideContext) -> None:
    context.register(
        "connector:local-readonly@1",
        ConnectorSpec(
            server_ref="local-readonly",
            readonly=True,
            tool_manifests=(
                ToolManifest(
                    name="LocalSourceOpen",
                    description="Open a local source file via the read-only connector.",
                    kind="external",
                    source=_CONNECTOR_SOURCE,
                    permission="read",
                    input_schema=CORE_TOOL_INPUT_SCHEMA,
                    timeout_ms=30_000,
                    budget=Budget(max_calls_per_turn=10, max_parallel=1),
                    dangerous=False,
                    is_concurrency_safe=True,
                    mutates_workspace=False,
                    parallel_safety="readonly",
                    available_in_modes=("plan", "act"),
                    tags=("connector", "source", "read"),
                    enabled_by_default=True,
                    opt_out=True,
                ),
            ),
        ),
    )
