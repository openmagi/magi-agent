"""Hermetic live provider for tests — no network, canned responses.

``FakeLiveProvider`` is functionally equivalent to ``StubLiveProvider`` from
``live_provider_pack.py`` but lives in the providers package alongside real
providers.  ``StubLiveProvider`` is preserved in ``live_provider_pack.py`` with
a re-export alias so existing tests continue to work unchanged.

Usage::

    from magi_agent.web_acquisition.providers.fake_provider import FakeLiveProvider
    provider = FakeLiveProvider(search_status="ok", search_records=[...])
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal


class FakeLiveProvider:
    """Fully configurable hermetic live provider for tests.

    Parameters
    ----------
    search_status:
        ``"ok"`` → return ``search_records`` (or default records).
        ``"timeout"`` → return ``{"status": "timeout"}``.
        ``"denied"`` → return ``{"status": "denied"}``.
    fetch_status / reader_status:
        Same shape for fetch/reader operations.
    search_records:
        Override the default search result list.
    fetch_content / reader_content:
        Override default content for fetch/reader.
    call_log:
        When provided, operation names are appended on each call — useful for
        asserting call order in router tests.
    raise_on:
        Set of operation names (``"search"``, ``"fetch"``, ``"reader"``) that
        should raise ``RuntimeError`` to simulate unexpected provider crashes.
    """

    openmagi_live_provider: Literal[True] = True

    def __init__(
        self,
        *,
        search_status: str = "ok",
        fetch_status: str = "ok",
        reader_status: str = "ok",
        search_records: list[dict[str, object]] | None = None,
        fetch_content: str = "Fake fetched content.",
        reader_content: str = "Fake reader content.",
        call_log: list[str] | None = None,
        raise_on: frozenset[str] = frozenset(),
    ) -> None:
        self._search_status = search_status
        self._fetch_status = fetch_status
        self._reader_status = reader_status
        self._search_records = search_records or [
            {
                "url": "https://docs.example.com/fake-result",
                "title": "Fake live search result",
                "snippet": "Canned fake search snippet.",
            }
        ]
        self._fetch_content = fetch_content
        self._reader_content = reader_content
        self._call_log = call_log
        self._raise_on = raise_on

    def search(self, request: object) -> Mapping[str, object]:
        if self._call_log is not None:
            self._call_log.append("search")
        if "search" in self._raise_on:
            raise RuntimeError("FakeLiveProvider.search configured to raise")
        return self._status_or_records(self._search_status, self._search_records)

    def fetch(self, request: object) -> Mapping[str, object]:
        if self._call_log is not None:
            self._call_log.append("fetch")
        if "fetch" in self._raise_on:
            raise RuntimeError("FakeLiveProvider.fetch configured to raise")
        return self._status_or_fetch(self._fetch_status, self._fetch_content)

    def reader(self, request: object) -> Mapping[str, object]:
        if self._call_log is not None:
            self._call_log.append("reader")
        if "reader" in self._raise_on:
            raise RuntimeError("FakeLiveProvider.reader configured to raise")
        return self._status_or_fetch(self._reader_status, self._reader_content)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _status_or_records(
        status: str,
        records: list[dict[str, object]],
    ) -> Mapping[str, object]:
        if status in {"timeout", "denied"}:
            return {"status": status}
        return {"results": records}

    @staticmethod
    def _status_or_fetch(status: str, content: str) -> Mapping[str, object]:
        if status in {"timeout", "denied"}:
            return {"status": status}
        return {
            "url": "https://docs.example.com/fake-fetch",
            "title": "Fake live fetch document",
            "content": content,
        }


__all__ = ["FakeLiveProvider"]
