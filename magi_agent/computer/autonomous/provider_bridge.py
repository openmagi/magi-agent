from __future__ import annotations

from collections.abc import Awaitable, Callable

SYSTEM_PROMPT = (
    "You control a macOS desktop. Each turn you receive a screenshot and an "
    "accessibility tree where every actionable element is tagged "
    '`[element_index N] AXRole "label"`. Reply with EXACTLY ONE JSON object and '
    "nothing else. Schema: {\"action\": one of "
    '"click"|"type"|"key"|"scroll"|"capture"|"done", "element_index": int '
    "(preferred for click/type), \"text\": str (for type), \"keys\": [str] (for "
    'key, e.g. ["cmd","c"]), "direction": "up"|"down" and "amount": int (for '
    'scroll), "summary": str (for done)}. Prefer element_index over raw pixel '
    "coordinates. Emit {\"action\": \"done\", \"summary\": ...} when the task is "
    "complete."
)


class BridgeError(RuntimeError):
    """Raised when a provider config cannot drive the computer-use loop."""


def build_step_messages(
    task: str, ax_tree: str, screenshot_b64: str, history: list[str]
) -> list[dict]:
    """Pure litellm message list for one computer-use step."""
    history_blob = "\n".join(f"- {h}" for h in history) if history else "(none yet)"
    user_text = (
        f"TASK: {task}\n\nACTIONS SO FAR:\n{history_blob}\n\n"
        f"ACCESSIBILITY TREE:\n{ax_tree}\n\n"
        "Choose the next single action as JSON."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"},
                },
            ],
        },
    ]


def build_chat_step(
    provider_config: object | None,
) -> Callable[[list[dict]], Awaitable[str]]:
    """Build an async one-shot vision completion callable. Lazy-imports litellm."""
    if provider_config is None:
        raise BridgeError("no provider configured; set a provider key for computer-use")
    model = getattr(provider_config, "litellm_model", None)
    api_key = getattr(provider_config, "api_key", None)
    if not model or not api_key:
        raise BridgeError("provider config missing litellm_model and/or api_key")

    async def _step(messages: list[dict]) -> str:
        import litellm  # noqa: PLC0415

        resp = await litellm.acompletion(model=model, api_key=api_key, messages=messages)
        return str(resp["choices"][0]["message"]["content"] or "")

    return _step
