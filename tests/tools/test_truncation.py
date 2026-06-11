"""Tests for magi_agent.tools.truncation — shared head+tail truncation.

Covers the pure helpers (``truncate_middle``, ``cap_text``, flag reader) and
the flag-ON/flag-OFF behavior of every call site wired in this PR:

- ``web_fetch`` / ``research_fact`` (``tools/web_search_tools.py``)
- ``document_read`` (``tools/document_tools.py``)

Hermetic — no network. HTTP calls are intercepted via monkeypatch on
``urllib.request.urlopen``; ``research_fact`` uses its injectable callables.

Default-OFF proof: with ``MAGI_HEADTAIL_TRUNCATION_ENABLED`` unset, the new
call sites are byte-identical to the legacy head-only slice (the pre-existing
``tests/test_web_search_tools.py::test_web_fetch_truncates_long_content``
also keeps passing unmodified).
"""

from __future__ import annotations

import io
import json
import urllib.request
from pathlib import Path

import pytest

from magi_agent.tools.truncation import (
    HEADTAIL_TRUNCATION_ENV,
    cap_text,
    is_headtail_truncation_enabled,
    truncate_middle,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeHTTPResponse:
    """Minimal file-like object that json.load() can consume."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._stream = io.BytesIO(json.dumps(payload).encode())

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def read(self, n: int = -1) -> bytes:
        return self._stream.read(n)


class _FakeOpener:
    """Callable standing in for urllib.request.urlopen with a canned payload."""

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __call__(self, request: urllib.request.Request, **_: object) -> _FakeHTTPResponse:
        return _FakeHTTPResponse(self.payload)


def _ctx(tmp_path: Path) -> object:
    from magi_agent.tools.context import ToolContext

    return ToolContext(
        botId="test-bot",
        sessionId="test-session",
        turnId="test-turn",
        workspaceRoot=str(tmp_path),
    )


# ---------------------------------------------------------------------------
# truncate_middle — pure unit tests
# ---------------------------------------------------------------------------


class TestTruncateMiddle:
    def test_short_content_unchanged_no_marker(self) -> None:
        assert truncate_middle("hello", 100) == "hello"

    def test_exact_boundary_unchanged(self) -> None:
        content = "x" * 100
        assert truncate_middle(content, 100) == content

    def test_long_content_keeps_head_and_tail(self) -> None:
        content = "".join(chr(ord("a") + (i % 26)) for i in range(1000))
        result = truncate_middle(content, 100)
        head_budget = (100 * 3) // 5  # 60
        tail_budget = 100 - head_budget  # 40
        assert result.startswith(content[:head_budget])
        assert result.endswith(content[-tail_budget:])

    def test_marker_contains_exact_elided_count(self) -> None:
        content = "z" * 1000
        result = truncate_middle(content, 100)
        elided = 1000 - 60 - 40
        assert f"[... {elided} chars elided" in result

    def test_result_length_is_budget_plus_marker(self) -> None:
        content = "q" * 5000
        result = truncate_middle(content, 200)
        head_budget = (200 * 3) // 5
        tail_budget = 200 - head_budget
        marker_len = len(result) - head_budget - tail_budget
        # marker is additive, bounded (~110 chars)
        assert 0 < marker_len < 130

    def test_max_chars_one_no_exception(self) -> None:
        result = truncate_middle("abcdef", 1)
        assert result.startswith("a")
        assert "elided" in result

    def test_max_chars_zero_clamped_no_exception(self) -> None:
        result = truncate_middle("abcdef", 0)
        assert result.startswith("a")
        assert "elided" in result

    def test_max_chars_negative_clamped_no_exception(self) -> None:
        result = truncate_middle("abcdef", -5)
        assert result.startswith("a")

    def test_empty_string_passthrough(self) -> None:
        assert truncate_middle("", 0) == ""
        assert truncate_middle("", 100) == ""

    def test_unicode_codepoint_safe(self) -> None:
        content = "한" * 1000
        result = truncate_middle(content, 100)
        assert result.startswith("한" * 60)
        assert result.endswith("한" * 40)


# ---------------------------------------------------------------------------
# Flag reader
# ---------------------------------------------------------------------------


class TestFlagReader:
    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", " On "])
    def test_truthy_values(self, value: str) -> None:
        assert is_headtail_truncation_enabled({HEADTAIL_TRUNCATION_ENV: value}) is True

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "banana"])
    def test_falsy_values(self, value: str) -> None:
        assert is_headtail_truncation_enabled({HEADTAIL_TRUNCATION_ENV: value}) is False

    def test_unset_is_off(self) -> None:
        assert is_headtail_truncation_enabled({}) is False

    def test_reads_os_environ_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(HEADTAIL_TRUNCATION_ENV, raising=False)
        assert is_headtail_truncation_enabled() is False
        monkeypatch.setenv(HEADTAIL_TRUNCATION_ENV, "1")
        assert is_headtail_truncation_enabled() is True


# ---------------------------------------------------------------------------
# cap_text — default-OFF byte-identity, flag-ON middle truncation
# ---------------------------------------------------------------------------


class TestCapTextDefaultOff:
    def test_flag_unset_byte_identical_to_legacy_slice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(HEADTAIL_TRUNCATION_ENV, raising=False)
        long = "w" * 10_000
        assert cap_text(long, 100) == (long[:100], True)

    def test_flag_zero_byte_identical_to_legacy_slice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(HEADTAIL_TRUNCATION_ENV, "0")
        long = "w" * 10_000
        assert cap_text(long, 100) == (long[:100], True)

    def test_short_content_not_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(HEADTAIL_TRUNCATION_ENV, raising=False)
        assert cap_text("short", 100) == ("short", False)

    def test_exact_boundary_not_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(HEADTAIL_TRUNCATION_ENV, raising=False)
        content = "x" * 100
        assert cap_text(content, 100) == (content, False)


class TestCapTextFlagOn:
    def test_flag_on_uses_middle_truncation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(HEADTAIL_TRUNCATION_ENV, "1")
        long = ("h" * 5000) + ("t" * 5000)
        capped, truncated = cap_text(long, 100)
        assert truncated is True
        assert capped == truncate_middle(long, 100)
        assert capped.endswith("t" * 40)

    def test_flag_on_short_content_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(HEADTAIL_TRUNCATION_ENV, "1")
        assert cap_text("short", 100) == ("short", False)

    def test_truncated_flag_identical_between_modes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        long = "y" * 200
        monkeypatch.delenv(HEADTAIL_TRUNCATION_ENV, raising=False)
        _, off_flag = cap_text(long, 100)
        monkeypatch.setenv(HEADTAIL_TRUNCATION_ENV, "1")
        _, on_flag = cap_text(long, 100)
        assert off_flag is True and on_flag is True

    def test_explicit_env_mapping_overrides_os_environ(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(HEADTAIL_TRUNCATION_ENV, raising=False)
        long = "m" * 1000
        capped, _ = cap_text(long, 100, env={HEADTAIL_TRUNCATION_ENV: "1"})
        assert "elided" in capped


# ---------------------------------------------------------------------------
# web_fetch call site
# ---------------------------------------------------------------------------


class TestWebFetchCallSite:
    def test_default_off_truncates_to_exactly_12000(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Flag unset → byte-identical to legacy head-only cap (len == 12 000)."""
        from magi_agent.tools.web_search_tools import web_fetch

        monkeypatch.delenv(HEADTAIL_TRUNCATION_ENV, raising=False)
        monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-" + "test-key")
        long_md = "x" * 20_000
        monkeypatch.setattr("urllib.request.urlopen", _FakeOpener({"data": {"markdown": long_md}}))

        result = web_fetch("https://example.com/long")

        assert len(result) == 12_000
        assert result == long_md[:12_000]

    def test_flag_on_keeps_tail_sentinel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from magi_agent.tools.web_search_tools import web_fetch

        monkeypatch.setenv(HEADTAIL_TRUNCATION_ENV, "1")
        monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-" + "test-key")
        sentinel = "FINAL-TOTAL-ROW-1234567890"
        long_md = ("x" * 20_000) + sentinel
        monkeypatch.setattr("urllib.request.urlopen", _FakeOpener({"data": {"markdown": long_md}}))

        result = web_fetch("https://example.com/long")

        assert result.startswith("x" * 100)
        assert "elided - output truncated" in result
        assert result.endswith(sentinel)

    def test_flag_on_short_content_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from magi_agent.tools.web_search_tools import web_fetch

        monkeypatch.setenv(HEADTAIL_TRUNCATION_ENV, "1")
        monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-" + "test-key")
        monkeypatch.setattr(
            "urllib.request.urlopen", _FakeOpener({"data": {"markdown": "short page"}})
        )

        assert web_fetch("https://example.com") == "short page"


# ---------------------------------------------------------------------------
# research_fact call site (injectable fetch_fn — no urlopen needed)
# ---------------------------------------------------------------------------


class TestResearchFactCallSite:
    @staticmethod
    def _search_fn(_query: str) -> dict[str, object]:
        return {
            "web": {
                "results": [
                    {
                        "url": "https://source.example.com/page",
                        "title": "Source",
                        "description": "snippet",
                    }
                ]
            }
        }

    def test_flag_on_brief_contains_source_tail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from magi_agent.tools.web_search_tools import research_fact

        monkeypatch.setenv(HEADTAIL_TRUNCATION_ENV, "1")
        sentinel = "CLOSING-BALANCE-987654"
        content = ("y" * 6000) + sentinel

        def fetch_fn(_url: str) -> dict[str, object]:
            return {"data": {"markdown": content}}

        brief = research_fact("q", search_fn=self._search_fn, fetch_fn=fetch_fn, n=1)

        assert sentinel in brief
        assert "elided - output truncated" in brief

    def test_default_off_brief_head_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from magi_agent.tools.web_search_tools import research_fact

        monkeypatch.delenv(HEADTAIL_TRUNCATION_ENV, raising=False)
        sentinel = "CLOSING-BALANCE-987654"
        content = ("y" * 6000) + sentinel

        def fetch_fn(_url: str) -> dict[str, object]:
            return {"data": {"markdown": content}}

        brief = research_fact("q", search_fn=self._search_fn, fetch_fn=fetch_fn, n=1)

        assert sentinel not in brief  # legacy head-only slice drops the tail
        assert "elided" not in brief
        assert ("y" * 4000) in brief  # exactly the legacy 4 000-char head


# ---------------------------------------------------------------------------
# document_read call site
# ---------------------------------------------------------------------------


class TestDocumentReadCallSite:
    def test_flag_on_text_ends_with_file_tail(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from magi_agent.tools.document_tools import document_read

        monkeypatch.setenv(HEADTAIL_TRUNCATION_ENV, "1")
        body = ("alpha " * 200) + "ZZZ-END-SENTINEL"
        (tmp_path / "long.txt").write_text(body, encoding="utf-8")

        result = document_read({"path": "long.txt", "maxChars": 100}, _ctx(tmp_path))

        assert result.status == "ok"
        output = result.output
        assert output["truncated"] is True  # type: ignore[index]
        text = output["text"]  # type: ignore[index]
        assert isinstance(text, str)
        assert text.endswith(body[-40:])  # tail budget of maxChars=100 is 40
        assert text.endswith("ZZZ-END-SENTINEL")
        assert "elided - output truncated" in text

    def test_default_off_text_is_head_only(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from magi_agent.tools.document_tools import document_read

        monkeypatch.delenv(HEADTAIL_TRUNCATION_ENV, raising=False)
        body = ("alpha " * 200) + "ZZZ-END-SENTINEL"
        (tmp_path / "long.txt").write_text(body, encoding="utf-8")

        result = document_read({"path": "long.txt", "maxChars": 100}, _ctx(tmp_path))

        assert result.status == "ok"
        output = result.output
        assert output["truncated"] is True  # type: ignore[index]
        assert output["text"] == body[:100]  # type: ignore[index]


# ---------------------------------------------------------------------------
# output_budget call site
# ---------------------------------------------------------------------------


class TestOutputBudgetCallSite:
    def test_preview_keeps_tail_and_digest_unchanged_regardless_of_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from magi_agent.tools.output_budget import budget_tool_result
        from magi_agent.tools.result import ToolResult

        sentinel = "GRAND-TOTAL-31337"
        text = ("b" * 10_000) + sentinel

        monkeypatch.delenv(HEADTAIL_TRUNCATION_ENV, raising=False)
        off = budget_tool_result(ToolResult(status="ok", output=text), llm_preview_chars=100)
        assert off.truncation.llm_preview_truncated is True
        assert isinstance(off.llm_preview, str)
        assert sentinel in off.llm_preview
        assert "chars elided" in off.llm_preview

        monkeypatch.setenv(HEADTAIL_TRUNCATION_ENV, "1")
        on = budget_tool_result(ToolResult(status="ok", output=text), llm_preview_chars=100)
        assert on.truncation.llm_preview_truncated is True
        assert isinstance(on.llm_preview, str)
        assert sentinel in on.llm_preview
        assert "chars elided" in on.llm_preview

        # digest / raw blob are computed from the raw result and are unaffected
        assert on.digest == off.digest
        assert on.raw_blob == off.raw_blob
        assert on.llm_preview == off.llm_preview

    def test_flag_on_within_budget_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from magi_agent.tools.output_budget import budget_tool_result
        from magi_agent.tools.result import ToolResult

        monkeypatch.setenv(HEADTAIL_TRUNCATION_ENV, "1")
        btr = budget_tool_result(
            ToolResult(status="ok", output="small output"), llm_preview_chars=4000
        )
        assert btr.truncation.llm_preview_truncated is False
        assert btr.llm_preview == "small output"


# ---------------------------------------------------------------------------
# archive_extract call site
# ---------------------------------------------------------------------------


class TestArchiveExtractCallSite:
    def _make_zip(self, tmp_path: Path, content: str) -> None:
        import zipfile

        with zipfile.ZipFile(tmp_path / "data.zip", "w") as zf:
            zf.writestr("notes.txt", content)

    def test_flag_on_entry_content_keeps_tail(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from magi_agent.tools.archive_tools import _MAX_ENTRY_CHARS, archive_extract

        monkeypatch.setenv(HEADTAIL_TRUNCATION_ENV, "1")
        sentinel = "LAST-LOG-LINE-0xDEAD"
        content = ("c" * (_MAX_ENTRY_CHARS + 5000)) + sentinel
        self._make_zip(tmp_path, content)

        result = archive_extract({"path": "data.zip", "readEntry": "notes.txt"}, _ctx(tmp_path))

        assert result.status == "ok"
        output = result.output
        assert output["truncated"] is True  # type: ignore[index]
        entry = output["entryContent"]  # type: ignore[index]
        assert isinstance(entry, str)
        assert entry.endswith(sentinel)
        assert "elided - output truncated" in entry

    def test_default_off_entry_content_head_only(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from magi_agent.tools.archive_tools import _MAX_ENTRY_CHARS, archive_extract

        monkeypatch.delenv(HEADTAIL_TRUNCATION_ENV, raising=False)
        content = "c" * (_MAX_ENTRY_CHARS + 5000)
        self._make_zip(tmp_path, content)

        result = archive_extract({"path": "data.zip", "readEntry": "notes.txt"}, _ctx(tmp_path))

        assert result.status == "ok"
        output = result.output
        assert output["truncated"] is True  # type: ignore[index]
        assert output["entryContent"] == content[:_MAX_ENTRY_CHARS]  # type: ignore[index]
