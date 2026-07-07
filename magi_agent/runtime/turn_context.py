"""Single value object describing one governed turn (top-level or child)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TurnContext:
    prompt: str
    session_id: str
    turn_id: str
    recipe: str | None = None
    # ``permission_cap`` (tool-allowlist cap), ``memory_mode``, and
    # ``permission_mode`` are THREE distinct, orthogonal authority knobs that
    # COMPOSE — do not collapse them. ``permission_cap`` bounds *which* tools a
    # turn may use; ``permission_mode`` is the per-turn ENFORCEMENT mode
    # (``default``/``ask`` vs ``bypassPermissions``) that A-1's resolver maps to
    # a scope. Default ``"default"`` = deny/ask (least privilege); bypass is set
    # ONLY when a caller explicitly and auditably requests it.
    permission_cap: frozenset[str] | None = None
    memory_mode: str = "normal"
    permission_mode: str = "default"
    provider: str | None = None
    model: str | None = None
    depth: int = 0
    budget_ms: int = 0
    # Optional resume-rehydration messages (``ResumeContext.initial_messages``).
    # Emitted by ``to_turn_input`` ONLY when non-empty so a normal turn's input
    # dict stays byte-identical to the pre-extraction ``{"prompt", ...}`` shape.
    initial_messages: tuple[dict[str, str], ...] = field(default_factory=tuple)
    # Optional image blocks in converter-dict shape (U5 / B1).
    # Each element: ``{"type": "image", "source": {"type": "base64",
    # "media_type": <str>, "data": <base64 str>}}``.
    # Emitted by ``to_turn_input`` ONLY when non-empty (shape-neutrality
    # invariant: a fresh-session turn dict stays byte-identical to the
    # pre-U5 shape when no images are present).
    image_blocks: tuple[dict[str, object], ...] = field(default_factory=tuple)

    def to_turn_input(self) -> dict[str, object]:
        turn_input: dict[str, object] = {
            "prompt": self.prompt,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "harness_state": self,
        }
        if self.initial_messages:
            turn_input["initial_messages"] = list(self.initial_messages)
        if self.image_blocks:
            turn_input["image_blocks"] = list(self.image_blocks)
        return turn_input
