from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Literal, TypeAlias


LoopAction: TypeAlias = Literal["ok", "soft_warning", "hard_escalation"]

_EXCLUDED_HASH_FIELDS: frozenset[str] = frozenset(
    ("task_progress", "progress", "metadata")
)
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?。！？\n])\s*")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class RepetitionResult:
    detected: bool
    pattern: str | None = None
    count: int | None = None


class RepetitionDetector:
    def __init__(
        self,
        *,
        min_pattern_len: int = 40,
        repeat_threshold: int = 3,
        check_interval: int = 200,
    ) -> None:
        self._text = ""
        self._last_check_len = 0
        self._min_pattern_len = min_pattern_len
        self._repeat_threshold = repeat_threshold
        self._check_interval = check_interval

    def feed(self, delta: str) -> RepetitionResult:
        self._text += delta
        if len(self._text) - self._last_check_len < self._check_interval:
            return RepetitionResult(detected=False)
        self._last_check_len = len(self._text)
        return self.check()

    def check(self) -> RepetitionResult:
        text = self._text
        if len(text) < self._min_pattern_len * self._repeat_threshold:
            return RepetitionResult(detected=False)

        suffix_result = self._check_repeated_suffix(text)
        if suffix_result.detected:
            return suffix_result
        return self._check_sentence_repetition(text)

    def get_text(self) -> str:
        return self._text

    def reset(self) -> None:
        self._text = ""
        self._last_check_len = 0

    def _check_repeated_suffix(self, text: str) -> RepetitionResult:
        max_pattern_len = min(
            len(text) // self._repeat_threshold,
            500,
        )
        candidates = list(
            range(max_pattern_len, self._min_pattern_len + 9, -10),
        )
        candidates.extend(
            range(
                min(max_pattern_len, self._min_pattern_len + 9),
                self._min_pattern_len - 1,
                -1,
            ),
        )

        for pattern_len in candidates:
            candidate = text[-pattern_len:]
            count = 0
            search_from = 0
            while search_from <= len(text) - pattern_len:
                index = text.find(candidate, search_from)
                if index == -1:
                    break
                count += 1
                if count >= self._repeat_threshold:
                    return RepetitionResult(
                        detected=True,
                        pattern=candidate[:80],
                        count=count,
                    )
                search_from = index + pattern_len
        return RepetitionResult(detected=False)

    def _check_sentence_repetition(self, text: str) -> RepetitionResult:
        sentences = [
            sentence
            for sentence in _SENTENCE_BOUNDARY_RE.split(text)
            if len(sentence) >= self._min_pattern_len
        ]
        if len(sentences) < self._repeat_threshold:
            return RepetitionResult(detected=False)

        counts: dict[str, int] = {}
        for sentence in sentences:
            normalized = _WHITESPACE_RE.sub(" ", sentence).strip()
            counts[normalized] = counts.get(normalized, 0) + 1

        for sentence, count in counts.items():
            if count >= self._repeat_threshold and len(sentence) >= self._min_pattern_len:
                return RepetitionResult(
                    detected=True,
                    pattern=sentence[:80],
                    count=count,
                )
        return RepetitionResult(detected=False)


@dataclass(frozen=True)
class LoopCheckResult:
    action: LoopAction
    count: int
    hash: str
    frequency_count: int | None = None

    def to_public_summary(self) -> dict[str, int | str]:
        summary: dict[str, int | str] = {
            "action": self.action,
            "count": self.count,
            "hash": self.hash,
        }
        if self.frequency_count is not None:
            summary["frequencyCount"] = self.frequency_count
        return summary


class ToolCallLoopDetector:
    def __init__(
        self,
        *,
        soft_threshold: int = 3,
        hard_threshold: int = 5,
        frequency_soft_threshold: int = 15,
        frequency_hard_threshold: int = 30,
    ) -> None:
        self._last_hash: str | None = None
        self._consecutive_count = 0
        self._soft_threshold = soft_threshold
        self._hard_threshold = hard_threshold
        self._frequency_soft_threshold = frequency_soft_threshold
        self._frequency_hard_threshold = frequency_hard_threshold
        self._tool_name_counts: dict[str, int] = {}

    @staticmethod
    def hash_call(tool_name: str, input_value: Any) -> str:
        stripped = _strip_excluded_fields(input_value)
        raw = f"{tool_name}:{_stable_json(stripped)}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    hashCall = hash_call

    def check(self, tool_name: str, input_value: Any) -> LoopCheckResult:
        call_hash = self.hash_call(tool_name, input_value)

        if call_hash == self._last_hash:
            self._consecutive_count += 1
        else:
            self._last_hash = call_hash
            self._consecutive_count = 1

        name_count = self._tool_name_counts.get(tool_name, 0) + 1
        self._tool_name_counts[tool_name] = name_count

        action: LoopAction = "ok"
        frequency_count: int | None = None

        if self._consecutive_count >= self._hard_threshold:
            action = "hard_escalation"
        elif name_count >= self._frequency_hard_threshold:
            action = "hard_escalation"
            frequency_count = name_count
        elif self._consecutive_count >= self._soft_threshold:
            action = "soft_warning"
        elif name_count >= self._frequency_soft_threshold:
            action = "soft_warning"
            frequency_count = name_count

        return LoopCheckResult(
            action=action,
            count=self._consecutive_count,
            hash=call_hash,
            frequency_count=frequency_count,
        )

    def reset(self) -> None:
        self._last_hash = None
        self._consecutive_count = 0
        self._tool_name_counts.clear()

    def get_tool_name_count(self, tool_name: str) -> int:
        return self._tool_name_counts.get(tool_name, 0)

    getToolNameCount = get_tool_name_count


def _strip_excluded_fields(input_value: Any) -> Any:
    if not isinstance(input_value, dict):
        return input_value
    return {
        key: value
        for key, value in input_value.items()
        if key not in _EXCLUDED_HASH_FIELDS
    }


def _stable_json(value: Any) -> str:
    # sort_keys=True so two semantically-identical calls whose dict args differ
    # only in key order hash IDENTICALLY (matches the "identical call" intent;
    # otherwise {"a":1,"b":2} and {"b":2,"a":1} would be treated as distinct
    # calls and never trip the loop guard).
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
