"""Live-SWE-style tool-synthesis activation + recipe block (default OFF).

Live-SWE-agent (arXiv 2511.13646) is a minimal coding agent plus two
prompt-level deltas: a "create your own tools" instruction block and a short
reflection nudge appended to every tool observation. On frontier models the
combination is a large win (Sonnet 4.5 SWE-bench Verified 62% -> 76% in the
paper's ablation); on weak models it HURTS badly (GPT-5-Nano 44% -> 14%).
Hence BOTH surfaces here are double-gated:

1. ``MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED`` (default OFF, strict truthy) — the
   single master flag, owned by ``magi_agent.config.env``.
2. Model tier — even with the flag ON, only models resolving to a frontier
   tier (``sota`` / ``reasoning``) in the ``ModelTierRegistry`` activate.
   Unknown models resolve to the registry's ``standard`` sentinel and stay
   inactive (fail-closed: weak-model protection wins over coverage).

Tier-gating limitation (documented, accepted): the registry only knows the
explicitly vetted (provider, model) records, so a frontier model absent from
``ModelTierRegistry.with_defaults()`` will NOT activate the feature until it
is added to the registry. The gate lives at flag-resolution level (here),
not at the ADK hook seam — the control plane has no per-call model identity,
so the runner passes its configured model label once at plane-build time.

This module is import-light: ``ModelTierRegistry`` is imported lazily inside
the resolver so importing it stays cold-start cheap and respects the
model_tiers import boundary.
"""

from __future__ import annotations

from collections.abc import Mapping

__all__ = [
    "FRONTIER_MODEL_TIERS",
    "TOOL_SYNTHESIS_INSTRUCTION_BLOCK",
    "TOOL_SYNTHESIS_NUDGE_TEXT",
    "TOOL_SYNTHESIS_TOOLS_DIR",
    "build_tool_synthesis_instruction_block",
    "tool_synthesis_nudge_active",
]

#: Tiers considered "frontier" for tool-synthesis activation.
FRONTIER_MODEL_TIERS: frozenset[str] = frozenset({"sota", "reasoning"})

#: Workspace-relative directory for model-authored helper scripts. Lives in the
#: magi-owned ``.magi`` namespace so helpers can never pollute the target
#: repository's patches/diffs.
TOOL_SYNTHESIS_TOOLS_DIR = ".magi/tools"

#: Short static reflection nudge appended to tool observations (per-step).
#: Kept lean — it rides on EVERY un-truncated tool result.
TOOL_SYNTHESIS_NUDGE_TEXT = (
    "Reflect on the trajectory so far: would a small custom Python script "
    "(saved under .magi/tools/ in the workspace) make the remaining work "
    "faster or more reliable? Basic shell commands working does not mean a "
    "purpose-built helper would not do better. If yes, create it and use it; "
    "otherwise continue."
)

#: System-prompt recipe block injected when the feature is active. Steers
#: toward search/analysis/verification helpers and explicitly AWAY from
#: building edit tools — magi already ships a native edit cascade
#: (FileEdit/PatchApply backed by ``magi_agent.coding.edit_matching``).
TOOL_SYNTHESIS_INSTRUCTION_BLOCK = (
    "<creating_your_own_tools>\n"
    "You may create your own task-specific tools: small Python CLI scripts "
    "that make repeated or error-prone steps faster and more reliable.\n"
    "- Save them under .magi/tools/ in the workspace — NEVER inside the "
    "target repository's working tree, so they can never leak into patches "
    "or diffs.\n"
    "- Good candidates: search/analysis helpers, output/log summarizers, "
    "batch verification scripts, and structured checks you would otherwise "
    "re-type as long shell pipelines.\n"
    "- Do NOT build file-editing tools; the native FileEdit/PatchApply tools "
    "already apply edits reliably — use them for all modifications.\n"
    "- Give every script informative stdout and clear error messages so a "
    "failed run tells you exactly what to fix, then invoke it via Bash.\n"
    "</creating_your_own_tools>"
)

# litellm provider prefix -> ModelTierRegistry provider label (identity unless
# listed). Mirrors ``cli.providers._LITELLM_PREFIX`` reversed.
_LITELLM_PREFIX_TO_REGISTRY_PROVIDER: dict[str, str] = {
    "fireworks_ai": "fireworks",
}


def _split_model_label(model_label: str) -> tuple[str, str] | None:
    """Split a litellm-form ``provider/model`` label into registry coordinates.

    Returns ``None`` when the label has no provider prefix (fail-closed).
    """
    text = (model_label or "").strip()
    if "/" not in text:
        return None
    prefix, _, model = text.partition("/")
    prefix = prefix.strip().lower()
    model = model.strip()
    if not prefix or not model:
        return None
    provider = _LITELLM_PREFIX_TO_REGISTRY_PROVIDER.get(prefix, prefix)
    return provider, model


def tool_synthesis_nudge_active(
    *,
    model_label: str,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Return ``True`` iff the tool-synthesis feature is active.

    Active requires BOTH:
    - ``MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED`` truthy (default OFF), AND
    - ``model_label`` (litellm ``provider/model`` form) resolving to a
      frontier tier (``sota``/``reasoning``) in the ``ModelTierRegistry``.

    Fail-closed: empty/malformed labels, unknown models (the registry's
    ``standard`` sentinel), and any resolution error return ``False``.
    """
    from magi_agent.config.env import (  # noqa: PLC0415 — avoid import cycle
        is_tool_synthesis_nudge_enabled,
    )

    if not is_tool_synthesis_nudge_enabled(env):
        return False

    coordinates = _split_model_label(model_label)
    if coordinates is None:
        return False
    provider, model = coordinates

    # Lazy import: keeps this module import-light and inside the model_tiers
    # import boundary (loaded only when the flag is actually ON).
    from magi_agent.runtime.model_tiers import ModelTierRegistry  # noqa: PLC0415

    try:
        resolved = ModelTierRegistry.with_defaults().resolve(
            provider=provider,
            model=model,
        )
    except Exception:  # noqa: BLE001 — label-validation failure → inactive.
        return False
    reason_codes = tuple(getattr(resolved, "reason_codes", ()) or ())
    if any("unknown_model" in code for code in reason_codes):
        return False
    return resolved.tier in FRONTIER_MODEL_TIERS


def build_tool_synthesis_instruction_block(
    *,
    model_label: str,
    env: Mapping[str, str] | None = None,
) -> str:
    """Return the recipe block when active, else ``""`` (fail-open to empty).

    Callers append the returned text to the system prompt verbatim; the empty
    string keeps prompt assembly byte-identical when the feature is off.
    """
    try:
        if not tool_synthesis_nudge_active(model_label=model_label, env=env):
            return ""
        return TOOL_SYNTHESIS_INSTRUCTION_BLOCK
    except Exception:  # noqa: BLE001 — prompt assembly must never break.
        return ""
