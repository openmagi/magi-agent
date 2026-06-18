"""Default-OFF system-prompt guidance blocks (Fable port D2-D4).

Three lean, env-gated fragments appended by ``build_cli_instruction``
(``magi_agent/cli/tool_runtime.py``), following the exact contract of
``web_research_guidance_block`` / ``build_tool_synthesis_instruction_block``:
each builder returns ``""`` when its flag is OFF, when its availability
condition is unmet, or on ANY error — so default prompt assembly stays
byte-identical.

Flags (all default OFF, independent — see ``magi_agent.config.env``):
  * ``MAGI_PROMPT_EXAMPLES_ENABLED``     -> <action_discipline_examples>
  * ``MAGI_PROMPT_SEARCH_RULES_ENABLED`` -> <search_decision> (also requires
    BRAVE_API_KEY + FIRECRAWL_API_KEY: never direct the model to absent tools)
  * ``MAGI_PROMPT_REDFLAGS_ENABLED``     -> <red_flags>
  * ``MAGI_RESEARCH_METHODOLOGY_ENABLED``   -> <research_methodology>
  * ``MAGI_AUTOMATION_METHODOLOGY_ENABLED`` -> <automation_methodology>

The two methodology blocks give the research and automation harnesses the kind
of domain workflow the coding harness already gets from
``CODING_DISCIPLINE_BLOCK`` / ``CODING_WORKFLOW_BLOCK`` — but as opt-in guidance
(the CLI is coding-first), not enforcement.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

__all__ = [
    "action_discipline_examples_block",
    "anti_rationalization_block",
    "automation_methodology_block",
    "research_methodology_block",
    "search_decision_block",
]

_ACTION_DISCIPLINE_EXAMPLES = (
    "<action_discipline_examples>\n"
    "Calibrate acting vs. asking with these contrasts:\n"
    "- Task says 'fix the failing test': run the suite, find the cause, fix "
    "it. Do NOT reply asking which test.\n"
    "- Asked 'what does this function do': read it and answer. Do NOT edit "
    "it.\n"
    "- A required credential or file is missing after you checked: report "
    "exactly what is missing. Do NOT silently substitute a guess.\n"
    "- You finished 3 of 4 subtasks: finish the 4th. Do NOT report partial "
    "work as done.\n"
    "- A fix worked on the first file: apply it to the remaining "
    "occurrences before reporting.\n"
    "</action_discipline_examples>"
)

_SEARCH_DECISION = (
    "<search_decision>\n"
    "Decide search vs. answer by rate of change and recognition:\n"
    "- Fast-changing facts (prices, versions, current officeholders, recent "
    "releases): search first.\n"
    "- Timeless or stable facts (math, algorithms, historical events, "
    "language syntax): answer directly; do not search.\n"
    "- Any name, product, or event you cannot confidently place: search "
    "before answering — partial recognition is not knowledge.\n"
    "Scale effort: single stable fact = 1 lookup; comparison or multi-part "
    "question = 3-5; deep research = 5-10 with cross-checks. If the first "
    "result fully answers, stop.\n"
    "</search_decision>"
)

_RED_FLAGS = (
    "<red_flags>\n"
    "If you notice one of these thoughts, stop and correct course:\n"
    "- 'This is probably enough' -> verify against the original ask before "
    "claiming done.\n"
    "- 'I should ask the user first' -> if the task already defines "
    "success, act; ask only when truly blocked.\n"
    "- 'The test probably passes' -> run it.\n"
    "- 'This file is probably where it lives' -> open it and confirm.\n"
    "- 'I will note this as a follow-up' -> if it is in scope, do it now.\n"
    "- 'That error looks unrelated' -> prove it before ignoring it.\n"
    "</red_flags>"
)


_RESEARCH_METHODOLOGY = (
    "<research_methodology>\n"
    "When the task is to research, investigate, or answer from sources:\n"
    "- Ground every factual claim in a source you actually opened this turn; "
    "do not answer fast-changing or specific factual questions from memory "
    "alone.\n"
    "- Corroborate load-bearing facts across at least two independent sources "
    "before stating them; a single source is a lead, not a conclusion.\n"
    "- Prefer primary sources (official docs, filings, the data itself) over "
    "secondary commentary; trace a claim to its origin.\n"
    "- Attribute each non-obvious fact to the source it came from, so the "
    "reader can verify it.\n"
    "- When sources conflict, report the disagreement instead of silently "
    "picking one.\n"
    "</research_methodology>"
)

_AUTOMATION_METHODOLOGY = (
    "<automation_methodology>\n"
    "When the task is a multi-step goal or automation:\n"
    "- State the concrete deliverable up front: the artifact or outcome that "
    "marks the task complete.\n"
    "- Plan the steps before acting, then work the plan; confirm each step's "
    "result before moving to the next.\n"
    "- Back every completion claim with evidence produced this turn (a file, a "
    "command output, a receipt) — not an assertion that it was done.\n"
    "- If a step is blocked, report exactly what is blocking and what you need; "
    "do not silently skip it or defer to later.\n"
    "- Do not report partial work as complete: finish every step the goal "
    "requires.\n"
    "</automation_methodology>"
)


def action_discipline_examples_block(env: Mapping[str, str] | None = None) -> str:
    """Gated ``<action_discipline_examples>`` fragment (D2). ``""`` when off."""
    try:
        from magi_agent.config.env import is_prompt_examples_enabled  # noqa: PLC0415

        source: Mapping[str, str] = os.environ if env is None else env
        if not is_prompt_examples_enabled(source):
            return ""
        return _ACTION_DISCIPLINE_EXAMPLES
    except Exception:  # noqa: BLE001
        return ""


def search_decision_block(env: Mapping[str, str] | None = None) -> str:
    """Gated ``<search_decision>`` fragment (D3).

    Requires the flag AND both web-provider keys (same availability rule as
    ``web_research_guidance_block``). ``""`` otherwise.
    """
    try:
        from magi_agent.config.env import (  # noqa: PLC0415
            is_prompt_search_rules_enabled,
        )

        source: Mapping[str, str] = os.environ if env is None else env
        if not is_prompt_search_rules_enabled(source):
            return ""
        if not source.get("BRAVE_API_KEY") or not source.get("FIRECRAWL_API_KEY"):
            return ""
        return _SEARCH_DECISION
    except Exception:  # noqa: BLE001
        return ""


def anti_rationalization_block(env: Mapping[str, str] | None = None) -> str:
    """Gated ``<red_flags>`` fragment (D4). ``""`` when off."""
    try:
        from magi_agent.config.env import is_prompt_redflags_enabled  # noqa: PLC0415

        source: Mapping[str, str] = os.environ if env is None else env
        if not is_prompt_redflags_enabled(source):
            return ""
        return _RED_FLAGS
    except Exception:  # noqa: BLE001
        return ""


def research_methodology_block(env: Mapping[str, str] | None = None) -> str:
    """Gated ``<research_methodology>`` fragment (H1). ``""`` when off."""
    try:
        from magi_agent.config.env import is_research_methodology_enabled  # noqa: PLC0415

        source: Mapping[str, str] = os.environ if env is None else env
        if not is_research_methodology_enabled(source):
            return ""
        return _RESEARCH_METHODOLOGY
    except Exception:  # noqa: BLE001
        return ""


def automation_methodology_block(env: Mapping[str, str] | None = None) -> str:
    """Gated ``<automation_methodology>`` fragment (H1). ``""`` when off."""
    try:
        from magi_agent.config.env import is_automation_methodology_enabled  # noqa: PLC0415

        source: Mapping[str, str] = os.environ if env is None else env
        if not is_automation_methodology_enabled(source):
            return ""
        return _AUTOMATION_METHODOLOGY
    except Exception:  # noqa: BLE001
        return ""
