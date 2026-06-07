from __future__ import annotations

import asyncio
import hashlib
import os
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .fork_messages import build_forked_messages
from .prompt_snapshot import FrozenPromptSnapshot


ForkCacheShareStatus = Literal["ok", "partial", "error", "disabled"]


class ForkCacheShareEvidence(BaseModel):
    """Evidence record for a fork cache sharing operation."""

    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    parent_turn_id: str = Field(alias="parentTurnId")
    child_count: int = Field(alias="childCount")
    shared_prefix_fingerprint: str = Field(alias="sharedPrefixFingerprint")
    #: Toolset names forwarded to the child executor for stripping.
    #: The concrete executor MUST strip these from the child tool list before
    #: the LLM call.  An empty tuple means no restriction.
    disabled_toolsets: tuple[str, ...] = Field(
        default=(), alias="disabledToolsets"
    )
    status: ForkCacheShareStatus
    elapsed_ms: float = Field(alias="elapsedMs")


class ChildResult(BaseModel):
    """Result from a single forked child execution."""

    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    directive: str
    status: Literal["ok", "error"]
    output: str = ""
    error_message: str | None = Field(default=None, alias="errorMessage")


class ForkRunner:
    """Runs fork children with shared prompt cache prefix.

    Creates one FrozenPromptSnapshot from parent, builds forked messages
    per child directive, and runs them concurrently via asyncio.gather().

    Tool-surface restriction contract
    ----------------------------------
    Callers that need to restrict the child's tool surface pass
    ``disabled_toolsets`` to ``fork()``.  The ``ForkRunner`` itself records
    and forwards the tuple to each child executor invocation; the *concrete*
    child executor is responsible for stripping those tool names from the
    tool list it builds before invoking the child LLM.  This makes the
    restriction enforced (not advisory): a misbehaving executor would have
    to explicitly ignore the parameter to bypass it.

    This mirrors the scheduler's ``CRON_DISABLED_TOOLSETS`` pattern: the
    fork runner is the transport layer; the executor is the enforcement layer.
    """

    def __init__(self, *, child_executor: Any = None) -> None:
        self._child_executor = child_executor
        self._enabled = os.environ.get("MAGI_FORK_CACHE_ENABLED", "").lower() in (
            "1", "true", "yes",
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def fork(
        self,
        *,
        parent_turn_id: str,
        system_prompt_blocks: list[dict[str, Any]],
        parent_assistant_message: dict[str, Any],
        child_directives: list[str],
        disabled_toolsets: tuple[str, ...] = (),
    ) -> tuple[list[ChildResult], ForkCacheShareEvidence]:
        """Run fork children, each with the shared prompt-cache prefix.

        Parameters
        ----------
        disabled_toolsets:
            Toolset names that MUST be stripped from the child's tool list by
            the concrete child executor before the LLM call.  Default ``()``
            leaves the existing callers unaffected.  The fork runner records
            this tuple in the evidence and passes it to every executor call;
            the executor is the enforcement point — it MUST NOT invoke the
            child LLM with any tool whose name appears in this tuple.
        """
        start = time.monotonic()

        if not self._enabled:
            return [], ForkCacheShareEvidence(
                parentTurnId=parent_turn_id,
                childCount=len(child_directives),
                sharedPrefixFingerprint="",
                disabledToolsets=disabled_toolsets,
                status="disabled",
                elapsedMs=0.0,
            )

        snapshot = FrozenPromptSnapshot.capture(system_prompt_blocks)

        child_message_sets: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []
        for directive in child_directives:
            restored_blocks = snapshot.restore()
            forked = build_forked_messages(
                parent_assistant_message=parent_assistant_message,
                directive=directive,
            )
            child_message_sets.append((restored_blocks, forked))

        if self._child_executor is not None:
            tasks = [
                self._child_executor(
                    system_prompt_blocks=blocks,
                    messages=messages,
                    directive=directive,
                    disabled_toolsets=disabled_toolsets,
                )
                for (blocks, messages), directive in zip(
                    child_message_sets, child_directives
                )
            ]
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        else:
            raw_results = [
                RuntimeError("no child executor configured")
                for _ in child_directives
            ]

        results: list[ChildResult] = []
        error_count = 0
        for directive, raw in zip(child_directives, raw_results):
            if isinstance(raw, BaseException):
                error_count += 1
                results.append(ChildResult(
                    directive=directive,
                    status="error",
                    errorMessage=str(raw),
                ))
            else:
                results.append(ChildResult(
                    directive=directive,
                    status="ok",
                    output=str(raw) if raw is not None else "",
                ))

        elapsed = (time.monotonic() - start) * 1000
        status: ForkCacheShareStatus
        if error_count == len(child_directives):
            status = "error"
        elif error_count > 0:
            status = "partial"
        else:
            status = "ok"

        evidence = ForkCacheShareEvidence(
            parentTurnId=parent_turn_id,
            childCount=len(child_directives),
            sharedPrefixFingerprint=snapshot.fingerprint,
            disabledToolsets=disabled_toolsets,
            status=status,
            elapsedMs=round(elapsed, 2),
        )

        return results, evidence
