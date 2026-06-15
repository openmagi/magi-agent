from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_DEFAULT_MODE = "deterministic"


@dataclass(frozen=True)
class CustomizeVerificationPolicy:
    """Resolved view of persisted verification overrides.

    The enforcement wiring (Phases 2-4) reads this off
    ``runtime.customize_verification_policy`` to decide which preset gates to
    contribute to the recipe-driven pre-final evidence gate. Phase 1 only
    constructs it; nothing consumes it yet.
    """

    enabled_presets: frozenset[str] = frozenset()
    enabled_recipes: frozenset[str] = frozenset()
    enabled_hooks: frozenset[str] = frozenset()
    modes: dict[str, str] = field(default_factory=dict)
    user_rules: str = ""

    @classmethod
    def from_overrides(cls, overrides: dict[str, Any]) -> "CustomizeVerificationPolicy":
        v = (overrides or {}).get("verification", {}) or {}
        presets = frozenset(
            x for x in v.get("harness_presets", []) if isinstance(x, str)
        )
        recipes = frozenset(x for x in v.get("recipes", []) if isinstance(x, str))
        hooks = frozenset(
            k for k, on in (v.get("hooks", {}) or {}).items() if isinstance(k, str) and on
        )
        modes = {
            k: m
            for k, m in (v.get("modes", {}) or {}).items()
            if isinstance(k, str) and isinstance(m, str)
        }
        raw_rules = (overrides or {}).get("user_rules", "")
        rules = raw_rules if isinstance(raw_rules, str) else ""
        return cls(presets, recipes, hooks, modes, rules)

    def is_enabled(self, preset_id: str) -> bool:
        return preset_id in self.enabled_presets

    def mode(self, preset_id: str) -> str:
        return self.modes.get(preset_id, _DEFAULT_MODE)
