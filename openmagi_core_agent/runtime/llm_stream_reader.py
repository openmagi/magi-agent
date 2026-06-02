from __future__ import annotations

from collections.abc import AsyncIterable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
import json
import re
from typing import Any, Literal, TypeAlias


StopReason: TypeAlias = Literal[
    "end_turn",
    "tool_use",
    "max_tokens",
    "stop_sequence",
    "refusal",
    "pause_turn",
]
OnError: TypeAlias = Callable[[str, BaseException], None]

_DEFAULT_USAGE = {"inputTokens": 0, "outputTokens": 0}
_ESCALATION_KEYS = frozenset(
    (
        "model",
        "modelLabel",
        "model_label",
        "provider",
        "providerLabel",
        "provider_label",
        "credentialRef",
        "credential_ref",
        "apiKey",
        "api_key",
    ),
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？\n])\s*")


@dataclass(frozen=True)
class LLMStreamReaderConfig:
    enabled: bool = False
    local_fake_provider_enabled: bool = False
    document_preview_chars: int = 4_000
    legacy_rendering_enabled: bool = False
    repetition_min_pattern_chars: int = 40
    repetition_threshold: int = 3
    repetition_check_interval_chars: int = 200


@dataclass(frozen=True)
class LLMStreamReaderResult:
    blocks: list[dict[str, Any]] = field(default_factory=list)
    public_events: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: StopReason | None = None
    usage: dict[str, Any] = field(default_factory=lambda: dict(_DEFAULT_USAGE))
    provider_request: dict[str, Any] | None = None
    skipped_reason: str | None = None


class LLMStreamReaderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class LLMStreamAbortError(RuntimeError):
    pass


@dataclass
class _ToolInput:
    id: str
    name: str
    input_json: str = ""


@dataclass(frozen=True)
class _DocumentDraftCandidate:
    format: Literal["md", "txt"]
    content: str
    filename: str | None = None


@dataclass(frozen=True)
class _DocumentDraftState:
    content_preview: str
    content_length: int
    truncated: bool
    filename: str | None = None


@dataclass(frozen=True)
class _RepetitionResult:
    detected: bool
    pattern: str | None = None
    count: int | None = None


class LLMStreamReader:
    def __init__(
        self,
        fake_provider: Any,
        *,
        config: LLMStreamReaderConfig | None = None,
    ) -> None:
        self._provider = fake_provider
        self._config = config or LLMStreamReaderConfig()

    async def read_one(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        system_prompt: str = "",
        tool_defs: Sequence[Mapping[str, Any]] | None = None,
        thinking_override: Mapping[str, Any] | None = None,
        trace_id: str | None = None,
        authoritative_model: bool = False,
        routing: Mapping[str, Any] | None = None,
        request_controlled_metadata: Mapping[str, Any] | None = None,
        abort_flag: object | None = None,
        on_error: OnError | None = None,
    ) -> LLMStreamReaderResult:
        if not self._config.enabled:
            return LLMStreamReaderResult(skipped_reason="disabled")
        if not self._config.local_fake_provider_enabled:
            return LLMStreamReaderResult(skipped_reason="local_fake_stream_provider_disabled")
        if getattr(self._provider, "openmagi_local_fake_provider", False) is not True:
            return LLMStreamReaderResult(skipped_reason="local_fake_stream_provider_untrusted")

        _raise_if_aborted(abort_flag)
        request = _build_provider_request(
            model=model,
            messages=messages,
            system_prompt=system_prompt,
            tool_defs=tool_defs or (),
            thinking_override=thinking_override,
            trace_id=trace_id,
            authoritative_model=authoritative_model,
            routing=routing,
            request_controlled_metadata=request_controlled_metadata,
        )

        try:
            stream = self._provider.stream(request)
        except BaseException as exc:
            _call_on_error(on_error, "llm_connect_failed", exc)
            raise LLMStreamReaderError(
                "llm_connect_failed",
                _sanitize_public_text(str(exc)) or "[redacted-provider-error]",
            ) from exc

        return await self._reduce_stream(
            stream,
            request=request,
            abort_flag=abort_flag,
            on_error=on_error,
        )

    async def _reduce_stream(
        self,
        stream: AsyncIterable[Mapping[str, Any]],
        *,
        request: dict[str, Any],
        abort_flag: object | None,
        on_error: OnError | None,
    ) -> LLMStreamReaderResult:
        text_by_index: dict[int, str] = {}
        thinking_by_index: dict[int, dict[str, str]] = {}
        tool_by_index: dict[int, _ToolInput] = {}
        document_draft_by_index: dict[int, _DocumentDraftState] = {}
        block_order: list[int] = []
        public_events: list[dict[str, Any]] = []
        stop_reason: StopReason | None = None
        usage: dict[str, Any] = dict(_DEFAULT_USAGE)
        repetition_aborted = False
        repetition_detector = _RepetitionDetector(
            min_pattern_chars=self._config.repetition_min_pattern_chars,
            threshold=self._config.repetition_threshold,
            check_interval_chars=self._config.repetition_check_interval_chars,
        )

        async for event in stream:
            _raise_if_aborted(abort_flag)
            kind = str(event.get("kind", ""))
            block_index = _block_index(event)

            if kind == "text_delta":
                previous = text_by_index.get(block_index)
                if previous is None:
                    block_order.append(block_index)
                    previous = ""
                delta = str(event.get("delta", ""))
                text_by_index[block_index] = previous + delta

                repetition = repetition_detector.feed(delta)
                if repetition.detected:
                    public_events.append(
                        {
                            "type": "warning",
                            "code": "repetition_detected",
                            "message": "Repeated text was detected and the local stream was stopped.",
                        },
                    )
                    repetition_aborted = True
                    break

                public_events.append({"type": "text_delta", "delta": delta})

            elif kind == "thinking_delta":
                previous = thinking_by_index.get(block_index)
                delta = str(event.get("delta", ""))
                if previous is None:
                    block_order.append(block_index)
                    thinking_by_index[block_index] = {
                        "thinking": delta,
                        "signature": "",
                    }
                else:
                    previous["thinking"] += delta

            elif kind == "thinking_signature":
                previous = thinking_by_index.get(block_index)
                signature = str(event.get("signature", ""))
                if previous is None:
                    block_order.append(block_index)
                    thinking_by_index[block_index] = {
                        "thinking": "",
                        "signature": signature,
                    }
                else:
                    previous["signature"] = signature

            elif kind == "tool_use_start":
                tool_by_index[block_index] = _ToolInput(
                    id=str(event.get("id", "")),
                    name=str(event.get("name", "")),
                )
                block_order.append(block_index)

            elif kind == "tool_use_input_delta":
                current = tool_by_index.get(block_index)
                if current is not None:
                    current.input_json += str(event.get("partial", ""))
                    draft = _draft_candidate_from_tool(current)
                    if draft is not None:
                        preview, truncated = _preview_tail(
                            draft.content,
                            self._config.document_preview_chars,
                        )
                        state = _DocumentDraftState(
                            content_preview=preview,
                            content_length=len(draft.content),
                            truncated=truncated,
                            filename=draft.filename,
                        )
                        if document_draft_by_index.get(block_index) != state:
                            event_payload: dict[str, Any] = {
                                "type": "document_draft",
                                "id": current.id,
                                "format": draft.format,
                                "contentPreview": preview,
                                "contentLength": len(draft.content),
                                "truncated": truncated,
                            }
                            if draft.filename:
                                event_payload["filename"] = _sanitize_public_text(
                                    draft.filename,
                                )
                            public_events.append(event_payload)
                            document_draft_by_index[block_index] = state

            elif kind == "message_end":
                raw_stop = event.get("stopReason", event.get("stop_reason"))
                stop_reason = _stop_reason(raw_stop)
                raw_usage = event.get("usage")
                if isinstance(raw_usage, Mapping):
                    usage = dict(raw_usage)

            elif kind == "error":
                code = str(event.get("code", "llm_stream_error"))
                message = str(event.get("message", "stream error"))
                error = LLMStreamReaderError(code, message)
                _call_on_error(on_error, code, error)
                raise error

            _raise_if_aborted(abort_flag)

        if repetition_aborted:
            stop_reason = "end_turn"

        blocks = _assemble_blocks(
            block_order=block_order,
            text_by_index=text_by_index,
            thinking_by_index=thinking_by_index,
            tool_by_index=tool_by_index,
        )
        return LLMStreamReaderResult(
            blocks=blocks,
            public_events=public_events,
            stop_reason=stop_reason,
            usage=usage,
            provider_request=request,
        )


class _RepetitionDetector:
    def __init__(
        self,
        *,
        min_pattern_chars: int,
        threshold: int,
        check_interval_chars: int,
    ) -> None:
        self._text = ""
        self._last_check_len = 0
        self._min_pattern_chars = min_pattern_chars
        self._threshold = threshold
        self._check_interval_chars = check_interval_chars

    def feed(self, delta: str) -> _RepetitionResult:
        self._text += delta
        if len(self._text) - self._last_check_len < self._check_interval_chars:
            return _RepetitionResult(detected=False)
        self._last_check_len = len(self._text)
        return self.check()

    def check(self) -> _RepetitionResult:
        text = self._text
        if len(text) < self._min_pattern_chars * self._threshold:
            return _RepetitionResult(detected=False)

        max_pattern_len = min(len(text) // self._threshold, 500)
        candidates = list(
            range(max_pattern_len, self._min_pattern_chars + 9, -10),
        )
        candidates.extend(
            range(min(max_pattern_len, self._min_pattern_chars + 9), self._min_pattern_chars - 1, -1),
        )

        for pattern_len in candidates:
            candidate = text[-pattern_len:]
            count = 0
            search_from = 0
            while search_from <= len(text) - pattern_len:
                index = text.find(candidate, search_from)
                if index < 0:
                    break
                count += 1
                if count >= self._threshold:
                    return _RepetitionResult(
                        detected=True,
                        pattern=candidate[:80],
                        count=count,
                    )
                search_from = index + pattern_len

        return self._check_sentence_repetition(text)

    def _check_sentence_repetition(self, text: str) -> _RepetitionResult:
        sentences = [
            sentence
            for sentence in _SENTENCE_SPLIT_RE.split(text)
            if len(sentence) >= self._min_pattern_chars
        ]
        if len(sentences) < self._threshold:
            return _RepetitionResult(detected=False)

        counts: dict[str, int] = {}
        for sentence in sentences:
            normalized = " ".join(sentence.split())
            counts[normalized] = counts.get(normalized, 0) + 1

        for sentence, count in counts.items():
            if count >= self._threshold and len(sentence) >= self._min_pattern_chars:
                return _RepetitionResult(
                    detected=True,
                    pattern=sentence[:80],
                    count=count,
                )
        return _RepetitionResult(detected=False)


def _build_provider_request(
    *,
    model: str,
    messages: Sequence[Mapping[str, Any]],
    system_prompt: str,
    tool_defs: Sequence[Mapping[str, Any]],
    thinking_override: Mapping[str, Any] | None,
    trace_id: str | None,
    authoritative_model: bool,
    routing: Mapping[str, Any] | None,
    request_controlled_metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "model": model,
        "messages": [dict(message) for message in messages],
    }
    if system_prompt:
        request["system"] = system_prompt
    if tool_defs:
        request["tools"] = [dict(tool_def) for tool_def in tool_defs]
    if thinking_override is not None:
        request["thinking"] = dict(thinking_override)
    if trace_id:
        request["traceId"] = trace_id
    if authoritative_model:
        request["authoritativeModel"] = True
    if routing is not None:
        request["routing"] = dict(routing)
    if _has_request_controlled_escalation(request_controlled_metadata):
        request["requestControlledEscalationRejected"] = True
    return request


def _assemble_blocks(
    *,
    block_order: Sequence[int],
    text_by_index: Mapping[int, str],
    thinking_by_index: Mapping[int, Mapping[str, str]],
    tool_by_index: Mapping[int, _ToolInput],
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    seen: set[int] = set()

    for block_index in block_order:
        if block_index in seen:
            continue
        seen.add(block_index)

        if block_index in text_by_index:
            blocks.append({"type": "text", "text": text_by_index[block_index]})
            continue

        thinking = thinking_by_index.get(block_index)
        if thinking is not None:
            blocks.append(
                {
                    "type": "thinking",
                    "thinking": thinking.get("thinking", ""),
                    "signature": thinking.get("signature", ""),
                },
            )
            continue

        tool = tool_by_index.get(block_index)
        if tool is not None:
            blocks.append(
                {
                    "type": "tool_use",
                    "id": tool.id,
                    "name": tool.name,
                    "input": _parse_tool_input(tool.input_json),
                },
            )

    return blocks


def _parse_tool_input(raw: str) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_malformed": True, "_raw": raw}


def _draft_candidate_from_tool(tool: _ToolInput) -> _DocumentDraftCandidate | None:
    if tool.name not in {"FileWrite", "DocumentWrite"}:
        return None

    parsed = _parsed_object(tool.input_json)
    if tool.name == "FileWrite":
        filename = _string_field(
            parsed,
            ("path", "file_path", "filepath", "filename"),
        ) or _first_json_string_field_prefix(
            tool.input_json,
            ("path", "file_path", "filepath", "filename"),
        )
        file_format = _document_format_from_path(filename)
        if file_format is None:
            return None
        content = _string_field(parsed, ("content",)) or _json_string_field_prefix(
            tool.input_json,
            "content",
        )
        if not content:
            return None
        return _DocumentDraftCandidate(
            filename=filename,
            format=file_format,
            content=content,
        )

    filename = _string_field(parsed, ("filename", "path")) or _first_json_string_field_prefix(
        tool.input_json,
        ("filename", "path"),
    )
    format_value = _string_field(parsed, ("format",)) or _json_string_field_prefix(
        tool.input_json,
        "format",
    )
    file_format = (
        format_value
        if format_value in {"md", "txt"}
        else _document_format_from_path(filename)
    )
    if file_format not in {"md", "txt"}:
        return None

    source = parsed.get("source") if parsed is not None else None
    content = _document_write_source_content(source) or _first_json_string_field_prefix(
        tool.input_json,
        ("content", "markdown", "text", "source"),
    )
    if not content:
        return None
    return _DocumentDraftCandidate(
        filename=filename,
        format=file_format,
        content=content,
    )


def _document_format_from_path(path: str | None) -> Literal["md", "txt"] | None:
    normalized = (path or "").split("?", 1)[0].split("#", 1)[0].lower()
    if normalized.endswith((".md", ".markdown")):
        return "md"
    if normalized.endswith(".txt"):
        return "txt"
    return None


def _document_write_source_content(source: Any) -> str | None:
    if isinstance(source, str):
        return source
    if isinstance(source, Mapping):
        return _string_field(source, ("content", "markdown", "text"))
    return None


def _parsed_object(raw: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _string_field(
    value: Mapping[str, Any] | None,
    keys: Sequence[str],
) -> str | None:
    if value is None:
        return None
    for key in keys:
        field_value = value.get(key)
        if isinstance(field_value, str):
            return field_value
    return None


def _first_json_string_field_prefix(raw: str, keys: Sequence[str]) -> str | None:
    for key in keys:
        value = _json_string_field_prefix(raw, key)
        if value is not None:
            return value
    return None


def _json_string_field_prefix(raw: str, key: str) -> str | None:
    escaped_key = re.escape(key)
    pattern = re.compile(rf'"{escaped_key}"\s*:\s*"')
    match = pattern.search(raw)
    if match is None:
        return None
    return _decode_json_string_prefix(raw, match.end())


def _decode_json_string_prefix(raw: str, start: int) -> str:
    value = ""
    escaped = False
    index = start
    while index < len(raw):
        char = raw[index]
        if escaped:
            if char == "n":
                value += "\n"
            elif char == "r":
                value += "\r"
            elif char == "t":
                value += "\t"
            elif char == "b":
                value += "\b"
            elif char == "f":
                value += "\f"
            elif char == "u":
                hex_value = raw[index + 1 : index + 5]
                if re.fullmatch(r"[0-9a-fA-F]{4}", hex_value):
                    value += chr(int(hex_value, 16))
                    index += 4
            else:
                value += char
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            break
        else:
            value += char
        index += 1
    return value


def _preview_tail(content: str, limit: int) -> tuple[str, bool]:
    sanitized = _sanitize_public_text(content)
    if limit < 1:
        return "", bool(sanitized)
    if len(sanitized) <= limit:
        return sanitized, False
    return sanitized[-limit:], True


def _sanitize_public_text(value: str) -> str:
    return "".join(
        char
        for char in value
        if char in "\n\r\t" or ord(char) >= 32
    )


def _block_index(event: Mapping[str, Any]) -> int:
    raw = event.get("blockIndex", event.get("block_index", 0))
    if isinstance(raw, int):
        return raw
    try:
        return int(str(raw))
    except ValueError:
        return 0


def _stop_reason(value: Any) -> StopReason | None:
    if value in {
        "end_turn",
        "tool_use",
        "max_tokens",
        "stop_sequence",
        "refusal",
        "pause_turn",
    }:
        return value
    return None


def _has_request_controlled_escalation(
    metadata: Mapping[str, Any] | None,
) -> bool:
    if metadata is None:
        return False
    for key in _ESCALATION_KEYS:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _raise_if_aborted(flag: object | None) -> None:
    if flag is None:
        return
    aborted = False
    for attr in ("aborted", "cancelled"):
        value = getattr(flag, attr, None)
        if callable(value):
            aborted = bool(value())
        elif value is not None:
            aborted = bool(value)
        if aborted:
            break
    if not aborted:
        is_set = getattr(flag, "is_set", None)
        if callable(is_set):
            aborted = bool(is_set())
    if not aborted:
        return

    reason = getattr(flag, "reason", None)
    if isinstance(reason, BaseException):
        raise reason
    raise LLMStreamAbortError("llm_stream_aborted")


def _call_on_error(
    on_error: OnError | None,
    code: str,
    error: BaseException,
) -> None:
    if on_error is not None:
        on_error(code, error)
