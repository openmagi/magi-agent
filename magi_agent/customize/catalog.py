from __future__ import annotations

from typing import Any

# The fixed set of runtime hook-point names exposed by the live runtime shell.
# OpenMagiRuntime has no hook_registry attribute — it is a thin shell that owns
# only tool_registry. Hook manifests are not surfaced via any runtime accessor;
# the /v1/app/skills endpoint (app_api._RUNTIME_HOOK_POINTS) uses this same
# hardcoded list. We source from there via import so both surfaces stay in sync.
from magi_agent.transport.app_api import _RUNTIME_HOOK_POINTS as _HOOK_POINTS

# Curated constants mirror REAL recipe modules under magi_agent/recipes/first_party/
# and the documented harness presets (docs/harness-schema.md). Phase 2 wires their
# selection to enforcement; Phase 1 surfaces them so the UI reaches parity.
RECIPES: list[dict[str, str]] = [
    {"id": "research", "title": "Research", "category": "research",
     "source": "docs/recipes.md",
     "description": "Multi-source research with grounded synthesis."},
    {"id": "coding_evidence_gate", "title": "Coding Evidence Gate", "category": "coding",
     "source": "magi_agent/recipes/first_party/coding",
     "description": "Require evidence before committing code changes."},
    {"id": "coding_mutation", "title": "Coding Mutation", "category": "coding",
     "source": "magi_agent/recipes/first_party/coding",
     "description": "Apply and verify workspace code mutations."},
    {"id": "general_automation", "title": "General Automation", "category": "task",
     "source": "magi_agent/recipes/first_party/general_automation",
     "description": "General multi-step task automation."},
    {"id": "memory_recall", "title": "Memory Recall", "category": "memory",
     "source": "magi_agent/recipes/first_party/memory_recall.py",
     "description": "Recall prior context from the memory ledger."},
    {"id": "self_improvement", "title": "Self Improvement", "category": "task",
     "source": "magi_agent/recipes/first_party/self_improvement.py",
     "description": "Gated self-improvement proposal loop."},
]

HARNESS_PRESETS: list[dict[str, str]] = [
    {"id": "answer_quality", "title": "Answer Quality", "category": "answer",
     "description": "Verify answers are complete and well-formed."},
    {"id": "fact_grounding", "title": "Fact Grounding", "category": "fact",
     "description": "Require factual claims to be grounded in sources."},
    {"id": "deterministic_evidence", "title": "Deterministic Evidence", "category": "fact",
     "description": "Deterministic evidence extraction for claims."},
    {"id": "coding_verification", "title": "Coding Verification", "category": "coding",
     "description": "Verify code changes against tests/build."},
    {"id": "source_authority", "title": "Source Authority", "category": "research",
     "description": "Weight sources by authority during research."},
    {"id": "hard_safety", "title": "Hard Safety", "category": "security",
     "description": "Always-on hard safety guardrails."},
]


def _recipe_entries() -> list[dict[str, Any]]:
    return [{**r, "enabled": True} for r in RECIPES]


def _preset_entries() -> list[dict[str, Any]]:
    return [{**p, "enabled": False} for p in HARNESS_PRESETS]


def _hook_entries(runtime: Any) -> list[dict[str, Any]]:
    # OpenMagiRuntime is a thin shell — it exposes only tool_registry; there is
    # no hook_registry attribute. Hook points are sourced from the same fixed
    # tuple that /v1/app/skills uses (_HOOK_POINTS, imported above). Each entry
    # is a builtin runtime-level hook point; none are user-opt-out-able, so all
    # are alwaysOn=True / category="security".
    entries: list[dict[str, Any]] = []
    for point_name in _HOOK_POINTS:
        entries.append(
            {
                "name": point_name,
                "point": point_name,  # already a plain camelCase string
                "title": point_name,
                "category": "security",
                "alwaysOn": True,
                "enabled": True,
            }
        )
    return entries


def _tool_entries(runtime: Any) -> list[dict[str, Any]]:
    # list_all() returns ToolManifest objects directly. The manifest only has
    # enabled_by_default; live enabled lives in ToolRegistration. We resolve
    # each registration to get the real enabled value — consistent with how
    # /api/tools (_public_tools) derives it in magi_agent/transport/tools.py.
    entries: list[dict[str, Any]] = []
    for manifest in runtime.tool_registry.list_all():
        registration = runtime.tool_registry.resolve_registration(manifest.name)
        enabled = registration.enabled if registration is not None else False
        source = manifest.source
        # source may be a ToolSource object (with .kind) or already a string
        # (e.g. in lightweight fakes). Normalise to string.
        source_str: str = source.kind if hasattr(source, "kind") else str(source)
        entries.append(
            {
                "name": manifest.name,
                "description": manifest.description if manifest.description else "",
                "enabled": bool(enabled),
                "source": source_str,
                "dangerous": bool(getattr(manifest, "dangerous", False)),
            }
        )
    return entries


def build_catalog(runtime: Any) -> dict[str, Any]:
    return {
        "verification": {
            "recipes": _recipe_entries(),
            "harnessPresets": _preset_entries(),
            "hooks": _hook_entries(runtime),
        },
        "tools": _tool_entries(runtime),
    }
