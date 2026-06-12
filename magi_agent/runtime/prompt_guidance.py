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
"""
from __future__ import annotations

import os
from collections.abc import Mapping

__all__ = [
    "action_discipline_examples_block",
    "anti_rationalization_block",
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
