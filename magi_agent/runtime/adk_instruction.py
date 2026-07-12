"""State-injection-safe ADK instruction wrapper.

Google ADK treats a plain-str ``Agent(instruction=...)`` as a session-state
template: ``inject_session_state`` substitutes every ``{identifier}``
placeholder from session state and raises ``KeyError`` for any identifier the
state does not contain (google/adk/utils/instructions_utils.py). Our hosted
system instruction is fully server-composed (operating frame plus an optional
activated SKILL.md body) and never uses state templating, while real skill
bodies legitimately contain brace-wrapped tokens; live incident 2026-07-12 on
canary 186bf3d7: a SKILL.md carrying SEC EDGAR URL templates ``{CIK}``/``{ACC}``
killed every turn with ``KeyError: 'Context variable not found: CIK'`` before
the model ran.

ADK's documented bypass is the ``InstructionProvider`` shape: when the
instruction is a callable, ``LlmAgent.canonical_instruction`` returns
``bypass_state_injection=True`` and the text reaches the model byte-identical.
``StateInjectionSafeInstruction`` is that callable, with ``__str__`` returning
the raw text so diagnostics and string-based assertions keep working.
"""

from __future__ import annotations

__all__ = ["StateInjectionSafeInstruction", "state_injection_safe_instruction"]


class StateInjectionSafeInstruction:
    """Callable instruction wrapper that ADK treats as an InstructionProvider.

    Intentionally NOT a ``str`` subclass: ADK routes ``isinstance(_, str)``
    instructions through session-state injection, which is exactly the path
    this wrapper exists to bypass.
    """

    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text

    def __call__(self, _readonly_context: object = None) -> str:
        return self.text

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return f"StateInjectionSafeInstruction(len={len(self.text)})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, StateInjectionSafeInstruction):
            return self.text == other.text
        if isinstance(other, str):
            return self.text == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.text)


def state_injection_safe_instruction(text: str) -> StateInjectionSafeInstruction:
    """Wrap a server-composed instruction so ADK never template-substitutes it."""
    return StateInjectionSafeInstruction(text)
