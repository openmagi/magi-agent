# tests/recipes/first_party/legal/test_rule_inject.py
from __future__ import annotations

from magi_agent.recipes.first_party.legal.rule_inject import (
    RULE_STATEMENTS,
    inject_rule,
)


def test_known_task_prepends_rule_statement() -> None:
    out = inject_rule("PROMPT BODY", task_id="abercrombie")
    assert out.startswith(RULE_STATEMENTS["abercrombie"])
    assert out.endswith("PROMPT BODY")
    assert "\n\n" in out


def test_unknown_task_is_passed_through_unchanged() -> None:
    assert inject_rule("PROMPT BODY", task_id="no_such_task") == "PROMPT BODY"
