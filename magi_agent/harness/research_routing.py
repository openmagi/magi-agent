from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from magi_agent.recipes.research_agents import (
    ResearchRouteDecision,
)


_KNOWN_PATH_RE = re.compile(
    r"(?:[\w.-]+/)+[\w.-]+|\b[\w.-]+\.(?:py|ts|tsx|js|jsx|md|mdx|json|ya?ml|toml|sql|sh|css|html|go|rs)\b",
    re.IGNORECASE,
)
_WEB_CURRENT_RE = re.compile(
    r"\b(current|latest|recent|today|yesterday|this week|public facts?|web|internet|online|news|release(?:d)?|newest)\b",
    re.IGNORECASE,
)
_PLAN_RE = re.compile(
    r"\b(implementation plan|research plan|design|proposal|architecture|architect|plan for|plan and|roadmap|approach)\b",
    re.IGNORECASE,
)
_VERIFIER_RE = re.compile(
    r"\b(verify|validate|validation|citation|citations|evidence|final answer|pass/fail|fact[- ]check|check)\b",
    re.IGNORECASE,
)
_STRONG_VERIFIER_RE = re.compile(
    r"\b(verify|validate|validation|citation|citations|evidence|final answer|pass/fail|fact[- ]check)\b",
    re.IGNORECASE,
)
_BROAD_RE = re.compile(
    r"\b(investigate|analy[sz]e|audit|search across|across (?:the )?repo|codebase|repository|find all|scan|trace|map out|deep dive|broad)\b",
    re.IGNORECASE,
)


def classify_research_route(
    user_text: str,
    available_tools: Iterable[object] = (),
) -> ResearchRouteDecision:
    text = user_text.strip()
    available_tool_names = _available_tool_names(available_tools)
    matched: list[str] = []
    has_known_path = _KNOWN_PATH_RE.search(text) is not None

    if has_known_path and _STRONG_VERIFIER_RE.search(text) is None:
        matched.append("known_local_path")
        return ResearchRouteDecision(
            agentType="direct",
            routeReason="known_local_lookup",
            matchedSignals=tuple(matched),
            availableToolNames=available_tool_names,
        )

    if _WEB_CURRENT_RE.search(text):
        matched.append("web_current")
        return ResearchRouteDecision(
            agentType="explore",
            routeReason="web_current_research",
            requiresWebTools=True,
            matchedSignals=tuple(matched),
            availableToolNames=available_tool_names,
        )

    if _VERIFIER_RE.search(text):
        matched.append("verification")
        return ResearchRouteDecision(
            agentType="verifier",
            routeReason="verification",
            matchedSignals=tuple(matched),
            availableToolNames=available_tool_names,
        )

    if _PLAN_RE.search(text):
        matched.append("implementation_planning")
        return ResearchRouteDecision(
            agentType="plan",
            routeReason="implementation_planning",
            matchedSignals=tuple(matched),
            availableToolNames=available_tool_names,
        )

    if _BROAD_RE.search(text):
        matched.append("broad_codebase_research")
        return ResearchRouteDecision(
            agentType="explore",
            routeReason="broad_codebase_research",
            matchedSignals=tuple(matched),
            availableToolNames=available_tool_names,
        )

    if has_known_path:
        matched.append("known_local_path")
        return ResearchRouteDecision(
            agentType="direct",
            routeReason="known_local_lookup",
            matchedSignals=tuple(matched),
            availableToolNames=available_tool_names,
        )

    return ResearchRouteDecision(
        agentType="direct",
        routeReason="simple_local_lookup",
        matchedSignals=(),
        availableToolNames=available_tool_names,
    )


def _available_tool_names(available_tools: Iterable[object]) -> tuple[str, ...]:
    names: list[str] = []
    for item in available_tools:
        name = _tool_name(item)
        if name and name not in names:
            names.append(name)
    return tuple(names)


def _tool_name(item: object) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, Mapping):
        raw_name = item.get("name")
        return raw_name if isinstance(raw_name, str) else None
    raw_name = getattr(item, "name", None)
    return raw_name if isinstance(raw_name, str) else None


__all__ = ["classify_research_route"]
