from __future__ import annotations

import re

from openmagi_core_agent.runtime.turn_utilities import RetryDecision, RetryRepairRule


_RESEARCH_PROOF_RE = re.compile(
    r"\[(?:RETRY|RULE):(?:CLAIM_CITATION|SOURCE_AUTHORITY|RESEARCH_PROOF)[^\]]*\]"
    r"|claim[-_\s]?citation|source[-_\s]?authority|research proof",
    re.IGNORECASE,
)
_GOAL_PROGRESS_RE = re.compile(r"GOAL_PROGRESS_EXECUTE_NEXT")
_INTERACTIVE_TOOL_RE = re.compile(r"INTERACTIVE_TOOL_REQUIRED")


def coding_edit_retry_repair_rules() -> tuple[RetryRepairRule, ...]:
    return (
        RetryRepairRule(
            kind="edit_apply_failed",
            error_code="not_unique",
            build_decision=lambda reason, error_code: _retry_decision(
                _edit_reflection_message(reason, error_code)
            ),
        ),
        RetryRepairRule(
            kind="edit_apply_failed",
            error_code="lazy_output",
            build_decision=lambda reason, error_code: _retry_decision(
                _edit_reflection_message(reason, error_code)
            ),
        ),
        RetryRepairRule(
            kind="edit_apply_failed",
            build_decision=lambda reason, error_code: _retry_decision(
                _edit_reflection_message(reason, error_code)
            ),
        ),
    )


def research_retry_repair_rules() -> tuple[RetryRepairRule, ...]:
    return (
        RetryRepairRule(
            kind="before_commit_blocked",
            reason_pattern=_RESEARCH_PROOF_RE,
            build_decision=lambda reason, _error_code: RetryDecision(
                action="resample",
                taxonomy="retry",
                tool_policy="text_only",
                hidden_user_message=_research_proof_rewrite_message(reason),
            ),
        ),
    )


def methodology_retry_repair_rules() -> tuple[RetryRepairRule, ...]:
    return (
        RetryRepairRule(
            kind="before_commit_blocked",
            reason_pattern=_GOAL_PROGRESS_RE,
            build_decision=lambda reason, _error_code: _retry_decision(
                _goal_progress_tool_first_message(reason)
            ),
        ),
    )


def automation_retry_repair_rules() -> tuple[RetryRepairRule, ...]:
    return (
        RetryRepairRule(
            kind="before_commit_blocked",
            reason_pattern=_INTERACTIVE_TOOL_RE,
            build_decision=lambda reason, _error_code: _retry_decision(
                _interactive_tool_first_message(reason)
            ),
        ),
    )


def default_recipe_retry_repair_rules() -> tuple[RetryRepairRule, ...]:
    return (
        *research_retry_repair_rules(),
        *methodology_retry_repair_rules(),
        *automation_retry_repair_rules(),
        *coding_edit_retry_repair_rules(),
    )


def _retry_decision(message: str) -> RetryDecision:
    return RetryDecision(
        action="resample",
        taxonomy="retry",
        tool_policy="normal",
        hidden_user_message=message,
    )


def _edit_reflection_message(reason: str, error_code: str | None) -> str:
    if error_code == "not_unique":
        return (
            "Your FileEdit failed: old_string appears more than once. "
            f"Reason: {reason}. "
            "Re-read the file and extend old_string with more surrounding context "
            "lines to make it unique."
        )
    if error_code == "lazy_output":
        return (
            "Your FileEdit was blocked: new_string contains a placeholder comment "
            "(e.g. '// ... existing code'). "
            f"Reason: {reason}. "
            "Write the complete replacement code; never use placeholder or "
            "abbreviated comments."
        )
    return (
        "Your FileEdit failed: old_string was not found in the file. "
        f"Reason: {reason}. "
        "Re-read the file with FileRead and retry with the exact old_string "
        "copied from the file content."
    )


def _research_proof_rewrite_message(reason: str) -> str:
    return "\n\n".join(
        (
            "Your previous draft was blocked by the research proof verifier.",
            f"Verifier reason:\n{reason}",
            "Rewrite the final answer as plain text only.",
            "Use only the already inspected sources listed in the verifier reason.",
            "Cite every factual claim with the source id that supports it, for example [src_1].",
            "Do not call tools, browse, search, fetch, or introduce new sources.",
            "Remove any claim that is not directly supported by the inspected source ids.",
        )
    )


def _goal_progress_tool_first_message(reason: str) -> str:
    return "\n".join(
        (
            "Your previous draft was blocked by the runtime goal-progress verifier.",
            f"Verifier reason:\n{reason}",
            "",
            "You must use the necessary tool or runtime action before writing another final answer.",
            "Do not answer with another promise, plan, dispatch announcement, or status update.",
            "Call the next required tool now. Examples include SpawnAgent, Browser, "
            "SocialBrowser, KnowledgeSearch, FileRead, FileWrite, FileDeliver, Bash, "
            "Calculation, or the exact tool required by the user request.",
            "After tool evidence is available, synthesize the actual result. If a hard "
            "blocker remains after concrete attempts, report that blocker with the evidence.",
        )
    )


def _interactive_tool_first_message(reason: str) -> str:
    return "\n".join(
        (
            "Your previous draft was blocked by the runtime interactive-work verifier.",
            f"Verifier reason:\n{reason}",
            "",
            "This request requires browser or GUI evidence.",
            "Use Browser or SocialBrowser for the next concrete action before writing another final answer.",
            "Do not answer with only text saying you will open, click, inspect, or test.",
            "If the Browser/SocialBrowser tools are not available in the exposed tool list, "
            "state that as the concrete blocker. Otherwise call the tool now.",
        )
    )


__all__ = [
    "automation_retry_repair_rules",
    "coding_edit_retry_repair_rules",
    "default_recipe_retry_repair_rules",
    "methodology_retry_repair_rules",
    "research_retry_repair_rules",
]
