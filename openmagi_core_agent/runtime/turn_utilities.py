from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import re
import unicodedata
from typing import Any, Literal, TypeAlias, TypeVar


RetryBlockKind: TypeAlias = Literal[
    "before_commit_blocked",
    "structured_output_invalid",
    "edit_apply_failed",
    "max_attempts_exceeded",
]
RetryToolPolicy: TypeAlias = Literal["normal", "text_only"]
RetryAction: TypeAlias = Literal["resample", "abort"]
RetryTaxonomy: TypeAlias = Literal["retry", "fail_open", "fail_closed"]
RouteMetaLanguage: TypeAlias = Literal["en", "ko", "ja", "zh", "es"]
CanonicalRoute: TypeAlias = Literal["direct", "subagent", "subagent->gate", "pipeline"]
CanonicalComplexity: TypeAlias = Literal["simple", "complex"]
T = TypeVar("T", bound=str)


@dataclass(frozen=True)
class RetryErrorMetadata:
    kind: str
    reason: str
    attempt: int
    error_code: str | None = None


@dataclass(frozen=True)
class RetryDecision:
    action: RetryAction
    taxonomy: RetryTaxonomy
    hidden_user_message: str = ""
    tool_policy: RetryToolPolicy = "normal"
    reason: str = ""


RetryRepairBuilder = Callable[[str, str | None], RetryDecision]


@dataclass(frozen=True)
class RetryRepairRule:
    kind: str
    build_decision: RetryRepairBuilder
    reason_pattern: re.Pattern[str] | None = None
    error_code: str | None = None

    def matches(self, *, kind: str, reason: str, error_code: str | None) -> bool:
        if kind != self.kind:
            return False
        if self.error_code is not None and self.error_code != error_code:
            return False
        if self.reason_pattern is not None and not self.reason_pattern.search(reason):
            return False
        return True


class RetryController:
    def __init__(
        self,
        *,
        max_attempts: int,
        repair_rules: Sequence[RetryRepairRule] = (),
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.max_attempts = max_attempts
        self.repair_rules = tuple(repair_rules)
        self.last_error: RetryErrorMetadata | None = None
        self.exhausted = False

    def reset(self) -> None:
        self.last_error = None
        self.exhausted = False

    def next(self, retry_input: Mapping[str, Any]) -> RetryDecision:
        kind = _string_value(retry_input.get("kind"))
        reason = _string_value(retry_input.get("reason"))
        attempt = _int_value(retry_input.get("attempt"))
        error_code = _optional_string_value(
            retry_input.get("errorCode", retry_input.get("error_code"))
        )

        self.last_error = RetryErrorMetadata(
            kind=kind,
            reason=reason,
            attempt=attempt,
            error_code=error_code,
        )

        if kind == "max_attempts_exceeded" or attempt >= self.max_attempts:
            self.exhausted = True
            return RetryDecision(
                action="abort",
                taxonomy="fail_closed",
                reason=reason,
            )

        self.exhausted = False

        for rule in self.repair_rules:
            if rule.matches(kind=kind, reason=reason, error_code=error_code):
                return rule.build_decision(reason, error_code)

        return RetryDecision(
            action="resample",
            taxonomy=classify_retry_taxonomy(kind, reason=reason, error_code=error_code),
            tool_policy="normal",
            hidden_user_message=(
                "Your previous draft was blocked by a runtime verifier. "
                f"Reason: {reason}. "
                "Produce a corrected answer that directly addresses the issue. "
                "Do not repeat the unsupported or invalid claim."
            ),
        )


_RETRY_CODES = frozenset(
    {
        "before_commit_blocked",
        "structured_output_invalid",
        "edit_apply_failed",
    }
)
_FAIL_OPEN_CODES = frozenset(
    {
        "provider_error",
        "model_provider_error",
        "rate_limited",
        "timeout",
        "timed_out",
        "runner_timeout",
        "route_disabled",
        "chat_route_disabled",
        "python_route_disabled",
        "kill_switch",
        "kill_switch_enabled",
        "kill_switch_active",
        "runner_exception",
        "runner_error",
        "context_overflow",
        "http_413",
        "empty_output",
        "empty_response",
        "truncation",
        "truncated",
        "max_tokens",
        "length",
    }
)
_FAIL_CLOSED_CODES = frozenset(
    {
        "max_attempts_exceeded",
        "validator_block",
        "verifier_blocked",
        "redaction_failure",
        "redaction_failed",
        "redaction_violation",
        "budget_exceeded",
        "session_budget_exceeded",
        "model_routing_invalid",
        "invalid_model",
        "unsupported_model",
        "missing_model",
        "user_interrupt",
        "user_interrupt_handoff",
        "turn_cancelled",
        "turn_canceled",
        "cancelled",
        "canceled",
        "aborterror",
        "abort_error",
    }
)


def classify_retry_taxonomy(
    code_or_kind: str | None,
    *,
    reason: str | None = None,
    error_code: str | None = None,
) -> RetryTaxonomy:
    candidates = (
        _normalize_code(error_code),
        _normalize_code(code_or_kind),
        _normalize_code(reason),
    )
    if any(candidate in _RETRY_CODES for candidate in candidates):
        return "retry"
    if any(candidate in _FAIL_CLOSED_CODES for candidate in candidates):
        return "fail_closed"
    if any(candidate in _FAIL_OPEN_CODES for candidate in candidates):
        return "fail_open"
    reason_text = reason or code_or_kind or ""
    if _REDACTION_RE.search(reason_text) or _MODEL_ROUTE_RE.search(reason_text):
        return "fail_closed"
    if _PROVIDER_RE.search(reason_text) or _TIMEOUT_RE.search(reason_text):
        return "fail_open"
    return "fail_closed"


_REDACTION_RE = re.compile(
    r"(?:^|[^a-z0-9])redaction(?:[^a-z0-9]+)?(?:failure|failed|violation)"
    r"(?:$|[^a-z0-9])",
    re.IGNORECASE,
)
_MODEL_ROUTE_RE = re.compile(
    r"\b(model routing invalid|unsupported model|invalid model|missing model|model_selection)\b",
    re.IGNORECASE,
)
_PROVIDER_RE = re.compile(
    r"\b(provider|upstream|rate.?limit|429|500|502|503|504|"
    r"econnreset|epipe|socket hang up|network error|fetch failed|"
    r"premature close|terminated|aborted)\b",
    re.IGNORECASE,
)
_TIMEOUT_RE = re.compile(r"\b(timed?\s*out|timeout|etimedout)\b", re.IGNORECASE)


def _normalize_code(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


_ROUTE_META_TAG_RE = re.compile(
    r"\[META\s*:\s*(?=[^\]]*\b(?:intent|domain|complexity|route)\s*=)[^\]]*\]\s*",
    re.IGNORECASE,
)
_ROUTE_META_PREFIX = "[META:"
_META_TAG_ONLY_RE = re.compile(r"^\[META\s*:[^\]]*\]", re.IGNORECASE)
_ROUTE_META_EXACT_RE = re.compile(r"^\[META\s*:\s*([\s\S]*?)\]$", re.IGNORECASE)

_ROUTE_ALIASES: dict[CanonicalRoute, tuple[str, ...]] = {
    "direct": ("direct", "directo", "직접", "바로", "直接", "直接处理", "直接処理"),
    "subagent": (
        "subagent",
        "sub-agent",
        "서브에이전트",
        "하위에이전트",
        "サブエージェント",
        "子代理",
        "subagente",
    ),
    "subagent->gate": (
        "subagent->gate",
        "subagent-gate",
        "서브에이전트->승인",
        "서브에이전트-승인",
        "하위에이전트->승인",
        "サブエージェント->承認",
        "子代理->审批",
        "subagente->aprobacion",
        "subagente->aprobación",
    ),
    "pipeline": (
        "pipeline",
        "파이프라인",
        "パイプライン",
        "流水线",
        "canalizacion",
        "canalización",
    ),
}

_COMPLEXITY_ALIASES: dict[CanonicalComplexity, tuple[str, ...]] = {
    "simple": ("simple", "단순", "간단", "簡単", "简单", "sencillo"),
    "complex": ("complex", "복잡", "複雑", "复杂", "complejo"),
}

_ROUTE_LABELS: dict[RouteMetaLanguage, dict[CanonicalRoute, str]] = {
    "en": {
        "direct": "direct",
        "subagent": "subagent",
        "subagent->gate": "subagent->gate",
        "pipeline": "pipeline",
    },
    "ko": {
        "direct": "직접",
        "subagent": "서브에이전트",
        "subagent->gate": "서브에이전트->승인",
        "pipeline": "파이프라인",
    },
    "ja": {
        "direct": "直接",
        "subagent": "サブエージェント",
        "subagent->gate": "サブエージェント->承認",
        "pipeline": "パイプライン",
    },
    "zh": {
        "direct": "直接",
        "subagent": "子代理",
        "subagent->gate": "子代理->审批",
        "pipeline": "流水线",
    },
    "es": {
        "direct": "directo",
        "subagent": "subagente",
        "subagent->gate": "subagente->aprobación",
        "pipeline": "canalización",
    },
}

_COMPLEXITY_LABELS: dict[RouteMetaLanguage, dict[CanonicalComplexity, str]] = {
    "en": {"simple": "simple", "complex": "complex"},
    "ko": {"simple": "단순", "complex": "복잡"},
    "ja": {"simple": "簡単", "complex": "複雑"},
    "zh": {"simple": "简单", "complex": "复杂"},
    "es": {"simple": "simple", "complex": "complejo"},
}

_INTENT_LABELS: dict[RouteMetaLanguage, dict[str, str]] = {
    "en": {
        "conversation": "conversation",
        "question": "question",
        "execution": "execution",
        "research": "research",
    },
    "ko": {
        "conversation": "대화",
        "question": "질문",
        "execution": "실행",
        "research": "리서치",
    },
    "ja": {
        "conversation": "会話",
        "question": "質問",
        "execution": "実行",
        "research": "リサーチ",
    },
    "zh": {
        "conversation": "对话",
        "question": "问题",
        "execution": "执行",
        "research": "研究",
    },
    "es": {
        "conversation": "conversación",
        "question": "pregunta",
        "execution": "ejecución",
        "research": "investigación",
    },
}

_DOMAIN_LABELS: dict[RouteMetaLanguage, dict[str, str]] = {
    "en": {
        "daily": "daily",
        "document writing": "document writing",
        "legal": "legal",
        "research": "research",
        "development": "development",
        "coding/testing": "coding/testing",
        "AI orchestration": "AI orchestration",
        "knowledge base": "knowledge base",
    },
    "ko": {
        "daily": "일상",
        "document writing": "문서작성",
        "legal": "법률",
        "research": "연구",
        "development": "개발",
        "coding/testing": "코딩/실험",
        "AI orchestration": "AI오케스트레이션",
        "knowledge base": "지식베이스",
    },
    "ja": {
        "daily": "日常",
        "document writing": "文書作成",
        "legal": "法務",
        "research": "研究",
        "development": "開発",
        "coding/testing": "コーディング/テスト",
        "AI orchestration": "AIオーケストレーション",
        "knowledge base": "ナレッジベース",
    },
    "zh": {
        "daily": "日常",
        "document writing": "文档写作",
        "legal": "法律",
        "research": "研究",
        "development": "开发",
        "coding/testing": "编码/测试",
        "AI orchestration": "AI编排",
        "knowledge base": "知识库",
    },
    "es": {
        "daily": "diario",
        "document writing": "redacción de documentos",
        "legal": "legal",
        "research": "investigación",
        "development": "desarrollo",
        "coding/testing": "programación/pruebas",
        "AI orchestration": "orquestación de IA",
        "knowledge base": "base de conocimiento",
    },
}

_INTENT_ALIASES: dict[str, tuple[str, ...]] = {
    "conversation": (
        "conversation",
        "chat",
        "대화",
        "会話",
        "对话",
        "conversación",
        "conversacion",
    ),
    "question": ("question", "질문", "質問", "问题", "pregunta"),
    "execution": (
        "execution",
        "execute",
        "task",
        "실행",
        "수행",
        "実行",
        "执行",
        "ejecución",
        "ejecucion",
    ),
    "research": ("research", "리서치", "조사", "研究", "investigación", "investigacion"),
}

_DOMAIN_ALIASES: dict[str, tuple[str, ...]] = {
    "daily": ("daily", "casual", "일상", "日常", "diario"),
    "document writing": (
        "document writing",
        "docs",
        "writing",
        "문서작성",
        "문서 작성",
        "文書作成",
        "文档写作",
        "redacción de documentos",
        "redaccion de documentos",
    ),
    "legal": ("legal", "law", "법률", "법무", "法務", "法律"),
    "research": ("research", "연구", "조사", "研究", "investigación", "investigacion"),
    "development": (
        "development",
        "coding",
        "code",
        "개발",
        "코딩",
        "開発",
        "开发",
        "desarrollo",
    ),
    "coding/testing": (
        "coding/testing",
        "coding / testing",
        "coding test",
        "coding tests",
        "coding experiment",
        "coding experiments",
        "코딩/실험",
        "코딩 실험",
        "코딩/테스트",
    ),
    "AI orchestration": (
        "AI orchestration",
        "AI오케스트레이션",
        "AI 오케스트레이션",
        "에이아이 오케스트레이션",
    ),
    "knowledge base": (
        "knowledge base",
        "kb",
        "지식베이스",
        "ナレッジベース",
        "知识库",
        "base de conocimiento",
    ),
}


def infer_route_meta_language(text: str) -> RouteMetaLanguage | None:
    visible = re.sub(r"\[META\s*:[^\]]*\]", " ", text, flags=re.IGNORECASE)
    if re.search(r"[\uac00-\ud7af]", visible):
        return "ko"
    if re.search(r"[\u3040-\u30ff]", visible):
        return "ja"
    if re.search(r"[\u4e00-\u9fff]", visible):
        return "zh"
    if re.search(r"[¿¡áéíóúñü]", visible, flags=re.IGNORECASE):
        return "es"
    if re.search(r"[A-Za-z]", visible):
        return "en"
    return None


def is_route_meta_tag(text: str) -> bool:
    fields = _parse_meta_tag_fields(text)
    if fields is None:
        return False
    return any(field[0].lower() in {"intent", "domain", "complexity", "route"} for field in fields)


def localize_route_meta_tag(tag: str, language: RouteMetaLanguage | None) -> str:
    if language is None:
        return tag
    fields = _parse_meta_tag_fields(tag)
    if fields is None:
        return tag
    localized = [
        f"{key}={_localize_meta_value(key, value, language)}"
        for key, value in fields
    ]
    return f"[META: {', '.join(localized)}]"


def normalize_route_value(value: str | None) -> CanonicalRoute | None:
    return _normalize_from_aliases(value, _ROUTE_ALIASES)


def normalize_complexity_value(value: str | None) -> CanonicalComplexity | None:
    return _normalize_from_aliases(value, _COMPLEXITY_ALIASES)


def normalize_user_visible_route_meta_tags(text: str) -> str:
    seen_route_meta = False
    language = infer_route_meta_language(_ROUTE_META_TAG_RE.sub(" ", text))

    def _replace(match: re.Match[str]) -> str:
        nonlocal seen_route_meta
        if seen_route_meta:
            return ""
        seen_route_meta = True
        matched = match.group(0)
        tag_match = _META_TAG_ONLY_RE.match(matched)
        if tag_match is None:
            return matched
        tag = tag_match.group(0)
        return f"{localize_route_meta_tag(tag, language)}{matched[len(tag):]}"

    return _ROUTE_META_TAG_RE.sub(_replace, text)


class UserVisibleRouteMetaFilter:
    def __init__(self) -> None:
        self.buffer = ""
        self.seen_route_meta = False
        self.strip_leading_whitespace_after_meta = False
        self.pending_first_route_meta: str | None = None

    def reset(self) -> None:
        self.buffer = ""
        self.seen_route_meta = False
        self.strip_leading_whitespace_after_meta = False
        self.pending_first_route_meta = None

    def filter(self, delta: str) -> str:
        if not delta:
            return ""
        if self.strip_leading_whitespace_after_meta:
            delta = re.sub(r"^\s+", "", delta)
            self.strip_leading_whitespace_after_meta = len(delta) == 0
            if not delta:
                return ""
        self.buffer += delta
        return self._drain(flush=False)

    def flush(self) -> str:
        return self._drain(flush=True)

    def _drain(self, *, flush: bool) -> str:
        out = ""
        while True:
            if self.pending_first_route_meta is not None:
                language = infer_route_meta_language(self.buffer)
                if language is not None or flush or self.buffer.strip():
                    out += localize_route_meta_tag(self.pending_first_route_meta, language)
                    self.pending_first_route_meta = None
                    continue
                return out

            start = _index_of_route_meta_start(self.buffer)
            if start == -1:
                if flush:
                    out += self.buffer
                    self.buffer = ""
                    return out
                keep = _trailing_route_meta_prefix_length(self.buffer)
                emit_length = len(self.buffer) - keep
                if emit_length > 0:
                    out += self.buffer[:emit_length]
                    self.buffer = self.buffer[emit_length:]
                return out

            if start > 0:
                out += self.buffer[:start]
                self.buffer = self.buffer[start:]

            end = self.buffer.find("]")
            if end == -1:
                if flush:
                    out += self.buffer
                    self.buffer = ""
                return out

            tag = self.buffer[: end + 1]
            if is_route_meta_tag(tag):
                rest = self.buffer[end + 1 :]
                if not self.seen_route_meta:
                    self.seen_route_meta = True
                    self.pending_first_route_meta = tag
                    self.buffer = rest
                    self.strip_leading_whitespace_after_meta = False
                    continue
                self.buffer = re.sub(r"^\s+", "", rest)
                self.strip_leading_whitespace_after_meta = len(self.buffer) == 0
                continue

            out += tag
            self.buffer = self.buffer[end + 1 :]


def extract_user_visible_text(events: Sequence[Mapping[str, Any]]) -> str:
    visible: list[str] = []
    route_filter = UserVisibleRouteMetaFilter()
    for event in events:
        event_type = event.get("type")
        if event_type in {"turn_start", "response_clear"}:
            route_filter.reset()
            visible.clear()
            continue
        if event_type == "text_delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                visible.append(route_filter.filter(delta))
            continue
        if event_type == "turn_end":
            visible.append(route_filter.flush())
    visible.append(route_filter.flush())
    return "".join(visible)


def extract_discovered_tool_names(messages: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    discovered: list[str] = []
    seen: set[str] = set()
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, Mapping) or block.get("type") != "tool_result":
                continue
            result_content = block.get("content")
            if not isinstance(result_content, list):
                continue
            for item in result_content:
                if not isinstance(item, Mapping) or item.get("type") != "tool_reference":
                    continue
                tool_name = item.get("tool_name")
                if not isinstance(tool_name, str) or tool_name in seen:
                    continue
                seen.add(tool_name)
                discovered.append(tool_name)
    return tuple(discovered)


@dataclass(frozen=True)
class PollingDetectorResult:
    is_polling: bool
    all_still_running: bool


_STATUS_CHECK_TOOLS = frozenset({"TaskGet"})
_RUNNING_STATUSES = frozenset({"running", "pending"})


def detect_polling_iteration(
    tool_names: Sequence[str],
    dispatched_results: Sequence[Mapping[str, Any]],
) -> PollingDetectorResult:
    if len(tool_names) == 0:
        return PollingDetectorResult(is_polling=False, all_still_running=False)
    if not all(name in _STATUS_CHECK_TOOLS for name in tool_names):
        return PollingDetectorResult(is_polling=False, all_still_running=False)

    all_still_running = all(_result_is_still_running(result) for result in dispatched_results)
    return PollingDetectorResult(is_polling=True, all_still_running=all_still_running)


def _result_is_still_running(result: Mapping[str, Any]) -> bool:
    if result.get("isError") is True:
        return False
    content = result.get("content")
    if not isinstance(content, str):
        return False
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, Mapping):
        return False
    status = parsed.get("status")
    return isinstance(status, str) and status in _RUNNING_STATUSES


def _localize_meta_value(
    key: str,
    value: str,
    language: RouteMetaLanguage,
) -> str:
    lower_key = key.lower()
    if lower_key == "route":
        route = normalize_route_value(value)
        return _ROUTE_LABELS[language][route] if route else value
    if lower_key == "complexity":
        complexity = normalize_complexity_value(value)
        return _COMPLEXITY_LABELS[language][complexity] if complexity else value
    if lower_key == "intent":
        intent = _normalize_from_aliases(value, _INTENT_ALIASES)
        return _INTENT_LABELS[language].get(intent, value) if intent else value
    if lower_key == "domain":
        domain = _normalize_from_aliases(value, _DOMAIN_ALIASES)
        return _DOMAIN_LABELS[language].get(domain, value) if domain else value
    return value


def _parse_meta_tag_fields(tag: str) -> list[tuple[str, str]] | None:
    match = _ROUTE_META_EXACT_RE.match(tag.strip())
    if match is None:
        return None
    body = match.group(1) or ""
    fields: list[tuple[str, str]] = []
    for part in body.split(","):
        eq = part.find("=")
        if eq == -1:
            continue
        key = part[:eq].strip()
        value = part[eq + 1 :].strip()
        if key and value:
            fields.append((key, value))
    return fields or None


def _normalize_from_aliases(
    value: str | None,
    aliases: Mapping[T, Sequence[str]],
) -> T | None:
    if not value:
        return None
    normalized = _normalize_alias(value)
    for canonical, values in aliases.items():
        if any(_normalize_alias(candidate) == normalized for candidate in values):
            return canonical
    return None


def _normalize_alias(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.strip().lower())
    without_marks = re.sub(r"[\u0300-\u036f]", "", decomposed)
    return re.sub(r"\s+", " ", without_marks)


def _index_of_route_meta_start(text: str) -> int:
    return text.upper().find("[META")


def _trailing_route_meta_prefix_length(text: str) -> int:
    upper = text.upper()
    max_len = min(len(_ROUTE_META_PREFIX), len(upper))
    for length in range(max_len, 0, -1):
        if _ROUTE_META_PREFIX.startswith(upper[-length:]):
            return length
    return 0


def _string_value(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional_string_value(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _int_value(value: object) -> int:
    return value if isinstance(value, int) else 0


__all__ = [
    "PollingDetectorResult",
    "RetryController",
    "RetryDecision",
    "RetryErrorMetadata",
    "RetryRepairRule",
    "UserVisibleRouteMetaFilter",
    "classify_retry_taxonomy",
    "detect_polling_iteration",
    "extract_discovered_tool_names",
    "extract_user_visible_text",
    "infer_route_meta_language",
    "is_route_meta_tag",
    "localize_route_meta_tag",
    "normalize_complexity_value",
    "normalize_route_value",
    "normalize_user_visible_route_meta_tags",
]
