from __future__ import annotations

from collections.abc import Callable
from typing import get_args

from magi_agent.harness.inference_scaling import TaskKind

__all__ = ["FixedClassifier", "TaskKindClassifier"]

_VALID: frozenset[str] = frozenset(get_args(TaskKind))
_SYSTEM_INSTRUCTION = (
    "You classify a user's request into exactly one task kind. "
    "Reply with ONLY one label from this set and nothing else: "
    + ", ".join(sorted(_VALID))
    + "."
)


class FixedClassifier:
    """Sync ClassifierPort that returns a precomputed label. Used to bridge an
    async-resolved TaskKind into the synchronous ``ClassifierPort.classify``
    contract consumed by ``classify_workflow_eligibility``. Invalid labels
    collapse to ``"general"``."""

    def __init__(self, label: str) -> None:
        self._label = label if label in _VALID else "general"

    def classify(self, message_text: str) -> str:
        return self._label


class TaskKindClassifier:
    """Async, LLM-backed TaskKind classifier. Mirrors the model-invocation
    contract of ``cli/readonly_classifier.py``. Fail-safe: any error or missing
    model resolves to ``"general"`` (not workflow-eligible)."""

    def __init__(self, *, model_factory: Callable[[], object] | None = None) -> None:
        self._model_factory = model_factory

    def _resolve_model(self) -> object | None:
        if self._model_factory is not None:
            try:
                return self._model_factory()
            except Exception:  # noqa: BLE001 — fail safe, never raise
                return None
        return None

    async def aclassify(self, message_text: str) -> str:
        """Return a TaskKind label for *message_text*; ``"general"`` on any
        failure or when no model is available."""
        model = self._resolve_model()
        if model is None:
            return "general"
        try:
            raw = await self._invoke_llm(model, message_text)
        except Exception:  # noqa: BLE001 — fail safe
            return "general"
        token = raw.strip().split()[0].strip().strip('".,') if raw.strip() else ""
        return token if token in _VALID else "general"

    @staticmethod
    async def _invoke_llm(model: object, prompt: str) -> str:
        from google.adk.models.llm_request import LlmRequest  # noqa: PLC0415
        from google.genai import types  # noqa: PLC0415

        llm_request = LlmRequest(
            config=types.GenerateContentConfig(system_instruction=_SYSTEM_INSTRUCTION),
            contents=[
                types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
            ],
        )
        collected: list[str] = []
        async for resp in model.generate_content_async(llm_request, stream=False):  # type: ignore[union-attr]
            if resp.content and resp.content.parts:
                for part in resp.content.parts:
                    if part.text:
                        collected.append(part.text)
        return "".join(collected)
