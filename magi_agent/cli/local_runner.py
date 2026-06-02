from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class _LocalAgent:
    tools: list[object] = field(default_factory=list)


class LocalCliRunner:
    """Model-free runner for the installed local CLI.

    The runner intentionally emits real ADK ``Event`` objects but does not call a
    model provider. This keeps ``magi`` useful out of the box while preserving
    the same adapter/projection path used by real runners.
    """

    def __init__(self, *, model: str | None = None) -> None:
        self.model = model or "local"
        self.agent = _LocalAgent()

    async def run_async(self, **kwargs: object):
        from google.adk.events import Event  # noqa: PLC0415
        from google.genai import types  # noqa: PLC0415

        prompt = _message_text(kwargs.get("new_message"))
        text = _local_response(prompt, model=self.model)
        yield Event(
            author="model",
            partial=True,
            content=types.Content(
                role="model",
                parts=[types.Part(text=text)],
            ),
        )


def build_local_cli_runner(*, model: str | None = None) -> LocalCliRunner:
    return LocalCliRunner(model=model)


def _message_text(value: object) -> str:
    parts = getattr(value, "parts", None)
    if not isinstance(parts, list):
        return ""
    text_parts: list[str] = []
    for part in parts:
        text = getattr(part, "text", None)
        if isinstance(text, str):
            text_parts.append(text)
    return "".join(text_parts).strip()


def _local_response(prompt: str, *, model: str) -> str:
    if prompt:
        return (
            "Local ADK runtime ready. I received your request, but no live model "
            f"provider is configured for this local install. Model: {model}. "
            "Set a provider or API proxy configuration to enable generated replies."
        )
    return (
        "Local ADK runtime ready. Type a request or configure a model provider "
        "to enable generated replies."
    )


__all__ = ["LocalCliRunner", "build_local_cli_runner"]
