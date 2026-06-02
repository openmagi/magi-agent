from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from queue import Empty, Queue
import re
from threading import Thread
import time
from typing import Any, Protocol, TypeVar


_MAX_TAGS = 3
_DEFAULT_TIMEOUT_MS = 3_000
_DEFAULT_CACHE_TTL_SECONDS = 60.0
_MAX_MESSAGE_CHARS = 2_000
_STRIP_CHARS = str.maketrans({char: "" for char in "`\"'*"})
_SPLIT_RE = re.compile(r"[,\n;]+")
_SAFE_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
_SECRET_OR_PATH_RE = re.compile(
    r"(?:"
    r"^\s*$|"
    r"\s|"
    r"[\\/'\"`$=]|"
    r"\.\.|"
    r"~|"
    r"://|"
    r":|"
    r"^sk-|"
    r"^xox[a-z]-|"
    r"^gh[opusr]_|"
    r"^github_pat_|"
    r"^AIza|"
    r"^gw_|"
    r"\bbearer\b|"
    r"api[_-]?key|"
    r"secret|"
    r"token|"
    r"password|"
    r"private[_-]?key|"
    r"credential"
    r")",
    re.IGNORECASE,
)

T = TypeVar("T")


@dataclass(frozen=True)
class ClassificationRequest:
    message: str
    available_tags: tuple[str, ...]
    timeout_ms: int


class ClassificationProvider(Protocol):
    def classify(self, request: ClassificationRequest) -> str:
        """Return comma, newline, or semicolon separated intent labels."""
        ...


class IntentClassifier:
    def __init__(
        self,
        *,
        enabled: bool = False,
        provider: ClassificationProvider | None = None,
        clock: Callable[[], float] | None = None,
        cache_ttl_seconds: float = _DEFAULT_CACHE_TTL_SECONDS,
    ) -> None:
        self.enabled = enabled
        self.provider = provider
        self._clock = clock or time.monotonic
        self._cache_ttl_seconds = max(0.0, cache_ttl_seconds)
        self._cache: dict[str, tuple[tuple[str, ...], float]] = {}

    def classify(
        self,
        message: str,
        available_tags: Iterable[str],
        *,
        timeout_ms: int = _DEFAULT_TIMEOUT_MS,
    ) -> tuple[str, ...]:
        allowed_tags = _normalize_allowed_tags(available_tags)
        if not allowed_tags:
            return ("general",)
        if not self.enabled or self.provider is None:
            return ("general",)

        normalized_timeout_ms = _normalize_timeout_ms(timeout_ms)
        if normalized_timeout_ms <= 0:
            return ("general",)

        cache_key = _cache_key(str(message), allowed_tags)
        now = self._clock()
        hit = self._cache.get(cache_key)
        if hit is not None:
            tags, cached_at = hit
            if now - cached_at < self._cache_ttl_seconds:
                return tags

        request = ClassificationRequest(
            message=str(message)[:_MAX_MESSAGE_CHARS],
            available_tags=allowed_tags,
            timeout_ms=normalized_timeout_ms,
        )
        started_at = now
        try:
            raw_output = _classify_with_deadline(
                self.provider,
                request,
                normalized_timeout_ms,
            )
        except Exception:
            return ("general",)
        if raw_output is None:
            return ("general",)

        finished_at = self._clock()
        if (finished_at - started_at) * 1000 > normalized_timeout_ms:
            return ("general",)

        tags = parse_tags(str(raw_output), allowed_tags)
        self._cache[cache_key] = (tags, finished_at)
        return tags


def parse_tags(raw: str, allowed_tags: Iterable[str]) -> tuple[str, ...]:
    allowed = set(_normalize_allowed_tags(allowed_tags))
    allowed.add("general")

    seen: set[str] = set()
    parsed: list[str] = []
    for token in _SPLIT_RE.split(str(raw).lower().translate(_STRIP_CHARS)):
        tag = _clean_tag(token)
        if tag is None or tag not in allowed or tag in seen:
            continue
        seen.add(tag)
        parsed.append(tag)
        if len(parsed) == _MAX_TAGS:
            break

    return tuple(parsed) if parsed else ("general",)


def filter_tools_by_intent(
    tools: Sequence[T],
    intent_tags: Iterable[str],
    max_total: int = 15,
) -> list[T]:
    core_tools: list[T] = []
    skill_tools: list[T] = []
    for tool in tools:
        if _tool_kind(tool) == "skill":
            skill_tools.append(tool)
        else:
            core_tools.append(tool)

    raw_intents = tuple(intent_tags)
    normalized_intents = tuple(
        tag for tag in (_clean_tag(raw) for raw in raw_intents) if tag is not None
    )
    include_all_skills = len(raw_intents) == 0 or "general" in normalized_intents
    if include_all_skills:
        selected_skills = skill_tools
    else:
        intent_set = set(normalized_intents)
        selected_skills = [
            tool
            for tool in skill_tools
            if intent_set.intersection(_tool_tags(tool))
        ]

    budget = max(0, int(max_total) - len(core_tools))
    ranked_skills = sorted(selected_skills, key=_tool_sort_key)
    return [*core_tools, *ranked_skills[:budget]]


def _normalize_timeout_ms(timeout_ms: int) -> int:
    try:
        return int(timeout_ms)
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_MS


def _classify_with_deadline(
    provider: ClassificationProvider,
    request: ClassificationRequest,
    timeout_ms: int,
) -> str | None:
    results: Queue[tuple[bool, str | Exception]] = Queue(maxsize=1)

    def run() -> None:
        try:
            results.put_nowait((True, provider.classify(request)))
        except Exception as exc:
            results.put_nowait((False, exc))

    worker = Thread(target=run, name="intent-classifier-provider", daemon=True)
    worker.start()

    try:
        succeeded, value = results.get(timeout=timeout_ms / 1000)
    except Empty:
        return None
    if not succeeded:
        if not isinstance(value, Exception):
            raise RuntimeError("intent classification provider failed")
        raise value
    return value


def _normalize_allowed_tags(tags: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in tags:
        tag = _clean_tag(raw)
        if tag is None or tag == "general" or tag in seen:
            continue
        seen.add(tag)
        normalized.append(tag)
    return tuple(normalized)


def _clean_tag(raw: object) -> str | None:
    tag = str(raw).lower().translate(_STRIP_CHARS).strip()
    if not _SAFE_TAG_RE.fullmatch(tag):
        return None
    if _SECRET_OR_PATH_RE.search(tag):
        return None
    return tag


def _cache_key(message: str, allowed_tags: tuple[str, ...]) -> str:
    return f"{','.join(sorted(allowed_tags))}|{message}"


def _tool_kind(tool: object) -> str | None:
    return _tool_field(tool, "kind")


def _tool_name(tool: object) -> str:
    name = _tool_field(tool, "name")
    return str(name) if name is not None else ""


def _tool_sort_key(tool: object) -> tuple[str, tuple[int, ...], str]:
    name = _tool_name(tool)
    return (name.casefold(), _ascii_locale_case_key(name), name)


def _ascii_locale_case_key(value: str) -> tuple[int, ...]:
    return tuple(1 if "A" <= char <= "Z" else 0 for char in value)


def _tool_tags(tool: object) -> tuple[str, ...]:
    tags = _tool_field(tool, "tags", ())
    if tags is None or isinstance(tags, str):
        return ()
    if not isinstance(tags, Iterable):
        return ()
    return tuple(tag for tag in (_clean_tag(raw) for raw in tags) if tag is not None)


def _tool_field(tool: object, field: str, default: Any = None) -> Any:
    if isinstance(tool, Mapping):
        return tool.get(field, default)
    return getattr(tool, field, default)
