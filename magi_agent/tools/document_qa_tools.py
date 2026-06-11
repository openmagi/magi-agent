"""DocumentQA tool — question-conditioned file QA via a sidecar model call.

HAL/smolagents ``TextInspectorTool`` pattern: take ``(path, question)``,
convert the file to markdown through the unified
:func:`~magi_agent.tools.file_markdown.convert_file_to_markdown` entry point,
send the converted content **plus the question** to a sidecar model call, and
return only a compact structured answer.  The orchestrating model never pays
main-context tokens for the raw file.

Gating: registered/bound only when both ``MAGI_FILE_TOOLS_ENABLED`` (outer
suite gate) and ``MAGI_DOCUMENT_QA_ENABLED`` (strict inner gate, default OFF
in all profiles) are enabled.

The sidecar call mirrors :func:`magi_agent.tools.image_tools._call_vision_model`:
a direct ``litellm.completion`` using
:func:`~magi_agent.cli.providers.resolve_provider_config`, fail-soft on any
exception.  ``MAGI_DOCUMENT_QA_MODEL`` overrides the model id so a cheap
(haiku-class) model can serve QA independently of the main model choice.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from magi_agent.config.flags import flag_str

from .context import ToolContext
from .file_markdown import convert_file_to_markdown, truncate_head_tail
from .result import ToolResult
from .spreadsheet_tools import (
    _SpreadsheetPolicyError,
    _base_metadata,
    _blocked_result,
    _error_result,
    _resolve_workspace_path,
    _workspace_root,
)

_SIDECAR_MAX_CONTENT_CHARS = 100_000  # head+tail cap on content sent to the sidecar
_ANSWER_MAX_CHARS = 6_000  # cap on answer text returned to main context
_FALLBACK_EXCERPT_CHARS = 4_000
_SIDECAR_MAX_TOKENS = 1_500
_SIDECAR_TIMEOUT_S = 90
_MIN_CONTENT_CHARS = 1_000

_FALLBACK_MODEL_ID = "anthropic/claude-sonnet-4-6"

_QA_SYSTEM_PROMPT = (
    "You answer questions about a document. Quote exact figures/strings. "
    "If the document does not contain the answer, say so explicitly — do not guess."
)

_QA_USER_TEMPLATE = (
    "Document (converted to markdown, possibly truncated in the middle):\n\n"
    "{content}\n\n"
    "Question: {question}\n\n"
    "Reply in this structure:\n"
    "1. Short answer\n"
    "2. Evidence (exact quotes + page/slide/sheet markers when present)\n"
    "3. Caveats"
)


def document_qa(arguments: Mapping[str, object], context: ToolContext) -> ToolResult:
    """Answer a question about a workspace file via a sidecar model call.

    Parameters
    ----------
    arguments:
        ``path``            — workspace-relative path to the file (required).
        ``question``        — the question to answer about the file (required).
        ``maxContentChars`` — optional cap on the converted content sent to the
                              sidecar, clamped to ``[1_000, 100_000]``.

    Invariant: the converted document text appears in **no** field of the
    returned ``ToolResult`` — only the compact answer (and, on sidecar
    failure, a small head+tail ``fallbackExcerpt``) reaches the main context.
    """
    tool_name = "document_qa"

    path_text = _str_arg(arguments, "path")
    if path_text is None:
        return _blocked_result(tool_name, "path_required")

    question = _str_arg(arguments, "question")
    if question is None or not question.strip():
        return _blocked_result(tool_name, "question_required")

    try:
        root = _workspace_root(context)
        resolved = _resolve_workspace_path(root, path_text, must_exist=True)
    except _SpreadsheetPolicyError as error:
        return _blocked_result(tool_name, error.reason_code)
    except OSError:
        return _error_result(tool_name, "document_qa_failed")

    max_content_chars = _SIDECAR_MAX_CONTENT_CHARS
    max_content_raw = arguments.get("maxContentChars")
    if isinstance(max_content_raw, int) and not isinstance(max_content_raw, bool):
        max_content_chars = min(
            max(max_content_raw, _MIN_CONTENT_CHARS), _SIDECAR_MAX_CONTENT_CHARS
        )

    conversion = convert_file_to_markdown(
        path_text, context, max_chars=max_content_chars
    )
    if conversion.status == "blocked":
        return _blocked_result(
            tool_name, conversion.error_code or "document_qa_blocked"
        )
    if conversion.status == "error":
        return _error_result(tool_name, conversion.error_code or "document_qa_failed")

    if not conversion.markdown.strip():
        return _error_result(tool_name, "document_empty")

    content, content_truncated = truncate_head_tail(
        conversion.markdown, max_content_chars
    )

    sidecar_used = True
    fallback_excerpt: str | None = None
    try:
        answer = _call_qa_model(content=content, question=question)
    except Exception as exc:  # noqa: BLE001 — fail-soft, mirrors image_tools
        sidecar_used = False
        answer = f"[document_qa sidecar call failed: {exc}]"
        fallback_excerpt, _ = truncate_head_tail(
            conversion.markdown, _FALLBACK_EXCERPT_CHARS
        )

    answer, _ = truncate_head_tail(answer, _ANSWER_MAX_CHARS)

    output: dict[str, object] = {
        "answer": answer,
        "sidecarUsed": sidecar_used,
        "contentDigest": conversion.content_digest,
        "sourceTool": conversion.source_tool,
        "contentTruncated": bool(conversion.truncated or content_truncated),
    }
    if fallback_excerpt is not None:
        output["fallbackExcerpt"] = fallback_excerpt

    return ToolResult(
        status="ok",
        output=output,
        llmOutput=output,
        transcriptOutput={
            "toolName": tool_name,
            "charCount": len(answer),
            "contentDigest": conversion.content_digest,
        },
        metadata={
            **_base_metadata(tool_name, permission_class="read", mutates_workspace=False),
            "contentDigest": conversion.content_digest,
            "sourceTool": conversion.source_tool,
            "sidecarUsed": sidecar_used,
            "pathRef": resolved.path_ref,
        },
    )


def _call_qa_model(
    *,
    content: str,
    question: str,
    completion_fn: Callable[..., object] | None = None,
) -> str:
    """Issue the sidecar QA call (litellm by default; injectable for tests).

    Mirrors ``image_tools._call_vision_model_via_litellm``: resolves the
    provider config for model id + api key, then issues a single completion.
    ``MAGI_DOCUMENT_QA_MODEL`` overrides the model id when set (cheap-model
    routing).  Raises on failure — :func:`document_qa` catches and degrades.
    """
    from magi_agent.cli.providers import resolve_provider_config  # noqa: PLC0415

    provider_cfg = resolve_provider_config()
    if provider_cfg is not None:
        model_id = provider_cfg.litellm_model
        api_key: str | None = provider_cfg.api_key
    else:
        model_id = _FALLBACK_MODEL_ID
        api_key = None

    model_override = (flag_str("MAGI_DOCUMENT_QA_MODEL") or "").strip()
    if model_override:
        model_id = model_override

    if completion_fn is None:
        import litellm  # noqa: PLC0415

        completion_fn = litellm.completion

    messages = [
        {"role": "system", "content": _QA_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _QA_USER_TEMPLATE.format(content=content, question=question),
        },
    ]
    resp = completion_fn(
        model=model_id,
        messages=messages,
        api_key=api_key,
        timeout=_SIDECAR_TIMEOUT_S,
        max_tokens=_SIDECAR_MAX_TOKENS,
    )
    text = (resp.choices[0].message.content or "").strip()  # type: ignore[union-attr]
    return text or "[no answer returned]"


def _str_arg(arguments: Mapping[str, object], name: str) -> str | None:
    value = arguments.get(name)
    if isinstance(value, str):
        return value
    return None


__all__ = ["document_qa"]
