"""First-party lean coding harness provider (no privilege, typed-ctx only).

Receives ONLY the narrow ``HarnessProvideContext`` (D5) and registers a
``ResolvedHarnessPack`` (tools/hooks/permission defaults). The projector
(``magi_agent/packs/harness_projection.py``) injects it into the live resolved
preset state on equal footing with first-party.
"""
from __future__ import annotations

from magi_agent.harness.resolved import ResolvedHarnessPack
from magi_agent.packs.context import HarnessProvideContext


def provide_harness(context: HarnessProvideContext) -> None:
    context.register(
        "harness:coding-lean@1",
        ResolvedHarnessPack(
            enabled=True,
            source="custom-plugin",
            components={
                "tools": ("FileRead", "FileEdit", "PatchApply"),
                "hooks": ("coding-verification",),
                "childAgent": (),
                "permissionDefaults": ("write_requires_act",),
            },
            opt_out_allowed=("childReview",),
        ),
    )
