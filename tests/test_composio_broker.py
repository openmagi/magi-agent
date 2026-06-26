from __future__ import annotations

from typing import Any


class _RecordingTransport:
    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.response = response if response is not None else {}
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        *,
        token: str,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append({"method": method, "url": url, "token": token, "json": json})
        return self.response


def _broker(transport, *, entity_id: str = "openmagi:user:u1:bot:b2"):
    from magi_agent.composio.broker import ComposioBrokerClient

    return ComposioBrokerClient(
        base_url="https://api.openmagi.ai/",
        token="magi_tok_123",
        entity_id=entity_id,
        transport=transport,
    )


def test_initiate_posts_connect_with_toolkit_and_entity() -> None:
    transport = _RecordingTransport(
        {"id": "conn_1", "status": "INITIATED", "redirect_url": "https://auth/x"}
    )
    result = _broker(transport).initiate("gmail")

    assert result["redirect_url"] == "https://auth/x"
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.openmagi.ai/v1/integrations/composio/connect"
    assert call["token"] == "magi_tok_123"
    assert call["json"] == {"toolkit": "gmail", "entity_id": "openmagi:user:u1:bot:b2"}


def test_status_gets_with_entity_query() -> None:
    transport = _RecordingTransport({"id": "conn_1", "status": "ACTIVE"})
    _broker(transport).status("conn_1")

    call = transport.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == (
        "https://api.openmagi.ai/v1/integrations/composio/connect/conn_1/status"
        "?entity_id=openmagi%3Auser%3Au1%3Abot%3Ab2"
    )


def test_list_unwraps_connections_array() -> None:
    transport = _RecordingTransport({"connections": [{"id": "conn_1"}]})
    items = _broker(transport).list()

    assert items == [{"id": "conn_1"}]
    assert transport.calls[0]["url"].startswith(
        "https://api.openmagi.ai/v1/integrations/composio/connections?entity_id="
    )


def test_list_returns_empty_on_missing_array() -> None:
    items = _broker(_RecordingTransport({})).list()
    assert items == []


def test_delete_issues_delete_with_entity_query() -> None:
    transport = _RecordingTransport({})
    _broker(transport).delete("conn_1")

    call = transport.calls[0]
    assert call["method"] == "DELETE"
    assert call["url"] == (
        "https://api.openmagi.ai/v1/integrations/composio/connection/conn_1"
        "?entity_id=openmagi%3Auser%3Au1%3Abot%3Ab2"
    )


def test_catalog_passes_filters_and_managed_flag() -> None:
    transport = _RecordingTransport({"items": []})
    _broker(transport).catalog(category="productivity", cursor=None, managed_only=False)

    url = transport.calls[0]["url"]
    assert url.startswith("https://api.openmagi.ai/v1/integrations/composio/catalog?")
    assert "category=productivity" in url
    assert "managed_only=0" in url
    assert "cursor=" not in url  # None params are dropped


def test_base_url_trailing_slash_is_normalized() -> None:
    transport = _RecordingTransport({})
    _broker(transport).initiate("gmail")

    assert (
        transport.calls[0]["url"]
        == "https://api.openmagi.ai/v1/integrations/composio/connect"
    )


def test_non_dict_transport_result_is_coerced_to_empty() -> None:
    class _BadTransport:
        def __call__(self, *a: Any, **k: Any) -> Any:
            return ["not", "a", "dict"]

    result = _broker(_BadTransport()).initiate("gmail")
    assert result == {}
