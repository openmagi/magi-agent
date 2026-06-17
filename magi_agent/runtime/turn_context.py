"""Single value object describing one governed turn (top-level or child)."""
from __future__ import annotations

from dataclasses import dataclass, field


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
    # Optional resume-rehydration messages (``ResumeContext.initial_messages``).
    # Emitted by ``to_turn_input`` ONLY when non-empty so a normal turn's input
    # dict stays byte-identical to the pre-extraction ``{"prompt", ...}`` shape.
    initial_messages: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def to_turn_input(self) -> dict[str, object]:
        turn_input: dict[str, object] = {
            "prompt": self.prompt,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "harness_state": self,
        }
        if self.initial_messages:
            turn_input["initial_messages"] = list(self.initial_messages)
        return turn_input
