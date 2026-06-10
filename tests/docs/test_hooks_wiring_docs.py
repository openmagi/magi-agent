"""Guards ``docs/hooks.md`` against drift from the real HookBus wiring (PR 17-PR4).

E5-e — ``docs/hooks.md`` previously described the HookBus as a generic
lifecycle *enforcement* mechanism while it was wired ``0`` ways into the live
turn loop. The HookBus is now PRESERVED and wired CC-style:

- user hooks load from ``settings.json``
  (``magi_agent/hooks/settings_loader.py``, mapping CC event names like
  ``PreToolUse`` / ``PostToolUse`` onto :class:`HookPoint` members);
- the command-executor bridge attaches those hooks onto the engine's ADK
  before/after-tool callbacks (``magi_agent/cli/hook_wiring.py`` →
  ``magi_agent/cli/engine.py``), default-OFF behind ``MAGI_USER_HOOKS_ENABLED``;
- http/llm executors are NOT yet wired (deferred to a later PR).

The doc must therefore (a) name the gate, (b) name the command-executor
wiring + its default-OFF state, (c) flag http/llm as not-yet-wired, and
(d) state the fixed callback order
(gate -> user hook -> control-plane -> runner_policy_route).
"""

from __future__ import annotations

from pathlib import Path

from magi_agent.hooks.manifest import HookPoint

ROOT = Path(__file__).resolve().parents[2]
HOOKS_DOC = ROOT / "docs" / "hooks.md"


def _doc_text() -> str:
    return HOOKS_DOC.read_text(encoding="utf-8")


def test_hooks_doc_names_the_default_off_gate() -> None:
    text = _doc_text()
    assert "MAGI_USER_HOOKS_ENABLED" in text
    assert "settings.json" in text
    # default-OFF must be stated, not implied.
    assert "default-OFF" in text or "default OFF" in text


def test_hooks_doc_describes_command_executor_bridge() -> None:
    text = _doc_text()
    # The only executor wired into the engine today is the command executor.
    assert "command executor" in text or "command-executor" in text
    # The bridge targets the engine before/after-tool callbacks.
    assert "before/after-tool" in text or "before-tool" in text


def test_hooks_doc_flags_http_llm_as_not_yet_wired() -> None:
    text = _doc_text().lower()
    assert "http" in text and "llm" in text
    assert "not yet wired" in text or "not-yet-wired" in text or "deferred" in text


def test_hooks_doc_states_the_fixed_callback_order() -> None:
    text = _doc_text()
    for token in (
        "gate",
        "user hook",
        "control-plane",
        "runner_policy_route",
    ):
        assert token in text, token


def test_hooks_doc_uses_real_pretooluse_posttooluse_event_names() -> None:
    text = _doc_text()
    assert "PreToolUse" in text
    assert "PostToolUse" in text


def test_hooks_doc_only_references_real_hook_point_names() -> None:
    """Every backtick `camelCase` hook point named in the doc must be a real
    HookPoint member (no invented points)."""
    import re

    text = _doc_text()
    real = {point.value for point in HookPoint}
    # Candidate hook-point tokens: backticked camelCase starting with a lower
    # word then an Uppercase word (e.g. `beforeToolUse`).
    candidates = set(re.findall(r"`([a-z][a-zA-Z]*[A-Z][a-zA-Z]*)`", text))
    hook_like = {
        c
        for c in candidates
        if c.startswith(
            ("before", "after", "on")
        )
    }
    invented = hook_like - real
    assert not invented, f"doc references non-existent hook points: {invented}"
