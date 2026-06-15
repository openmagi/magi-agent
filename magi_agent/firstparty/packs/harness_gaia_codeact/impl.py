"""First-party CodeAct harness provider (no privilege, typed-ctx only).

Receives ONLY the narrow ``HarnessProvideContext`` (D5) and registers a
``ResolvedHarnessPack`` (tools/hooks) on equal footing with first-party. Modeled
on ``magi_agent/firstparty/packs/harness_coding_lean/impl.py``.

It bundles the CodeAct lever: the ``PersistentPython`` tool plus a
``codeact-guidance`` hook advertising the general discipline — carry intermediate
results in variables across steps, prefer one richer code step over many small
tool calls, and base answers on printed program output. GENERAL agent hygiene:
no GAIA/benchmark-specific text (the harness ref name is the only ``gaia`` token,
matching the design doc's chosen ref ``harness:gaia-codeact@1``; the components
and discipline are domain-neutral).
"""
from __future__ import annotations

from magi_agent.harness.resolved import ResolvedHarnessPack
from magi_agent.packs.context import HarnessProvideContext

HARNESS_REF = "harness:gaia-codeact@1"
CODEACT_GUIDANCE_HOOK_NAME = "codeact-guidance"
PERSISTENT_PYTHON_TOOL_NAME = "PersistentPython"


def provide_harness(context: HarnessProvideContext) -> None:
    context.register(
        HARNESS_REF,
        ResolvedHarnessPack(
            enabled=True,
            source="custom-plugin",
            components={
                "tools": (PERSISTENT_PYTHON_TOOL_NAME,),
                "hooks": (CODEACT_GUIDANCE_HOOK_NAME,),
                "childAgent": (),
                "permissionDefaults": (),
            },
        ),
    )


__all__ = [
    "CODEACT_GUIDANCE_HOOK_NAME",
    "HARNESS_REF",
    "PERSISTENT_PYTHON_TOOL_NAME",
    "provide_harness",
]
