from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from magi_agent.computer.autonomous.cua_pure import parse_action
from magi_agent.computer.autonomous.provider_bridge import build_step_messages
from magi_agent.computer.autonomous.safety_hooks import is_sensitive_action


@dataclass(frozen=True)
class ComputerRunResult:
    status: str
    summary: str = ""
    steps_used: int = 0
    error_code: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


def _action_target(action: dict[str, object]) -> str:
    parts = [str(action.get(k, "")) for k in ("target", "text", "label")]
    keys = action.get("keys")
    if isinstance(keys, list):
        parts.extend(str(k) for k in keys)
    return " ".join(p for p in parts if p)


class ComputerEngine:
    """Autonomous macOS computer-use loop with injected collaborators."""

    def __init__(
        self,
        *,
        backend: object,
        chat_step: Callable[[list[dict]], Awaitable[str]],
        consent: Callable[[str], Awaitable[bool]],
    ) -> None:
        self._backend = backend
        self._chat_step = chat_step
        self._consent = consent

    async def run(self, *, task: str, max_steps: int) -> ComputerRunResult:
        history: list[str] = []
        steps_used = 0
        try:
            for _ in range(max_steps):
                cap = await self._backend.capture()  # type: ignore[attr-defined]
                messages = build_step_messages(
                    task=task,
                    ax_tree=cap.ax_tree,
                    screenshot_b64=cap.screenshot_b64,
                    history=history,
                )
                reply = await self._chat_step(messages)
                steps_used += 1
                try:
                    action = parse_action(reply)
                except ValueError as exc:
                    history.append(f"INVALID action ignored: {exc}")
                    continue

                kind = str(action.get("action"))
                if kind == "done":
                    return ComputerRunResult(
                        status="ok",
                        summary=str(action.get("summary", "")),
                        steps_used=steps_used,
                    )

                target = _action_target(action)
                if is_sensitive_action(kind, target):
                    approved = await self._consent(f"{kind}: {target}")
                    if not approved:
                        history.append(f"DENIED sensitive action {kind}: {target}")
                        continue

                try:
                    await self._backend.dispatch(  # type: ignore[attr-defined]
                        action, pid=cap.pid, window_id=cap.window_id
                    )
                    history.append(f"did {kind}: {target}".strip())
                except Exception as exc:  # noqa: BLE001 - per-step failures are recoverable
                    history.append(f"action {kind} failed: {exc}")

            return ComputerRunResult(
                status="ok",
                summary="step budget exhausted before task signalled done",
                steps_used=steps_used,
            )
        except Exception as exc:  # noqa: BLE001 - whole-run failure
            return ComputerRunResult(
                status="error",
                error_code="computer_run_failed",
                summary=str(exc),
                steps_used=steps_used,
            )
