"""Single value object describing one governed turn (top-level or child)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TurnContext:
    prompt: str
    session_id: str
    turn_id: str
    recipe: str | None = None
    permission_cap: frozenset[str] | None = None
    memory_mode: str = "normal"
    provider: str | None = None
    model: str | None = None
    depth: int = 0
    budget_ms: int = 0

    def to_turn_input(self) -> dict[str, object]:
        return {
            "prompt": self.prompt,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "harness_state": self,
        }
