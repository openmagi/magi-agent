"""H-36 — ``JinaReaderProvider`` releases its owned ``httpx.Client``.

When constructed without an injected ``client=``, the provider builds a
default ``httpx.Client`` and hands it to the inner ``LiveFetchProvider``.
That client used to leak — nothing ever called ``close()`` on it. Per
REVIEW-A ``review/tools-web-research.md`` L3 (H-36 grab-bag item 4),
the provider now exposes a ``close()`` method (and a context-manager
seam) so the owning caller can release the connection pool.

This module locks the contract:

1. ``close()`` closes the owned default client.
2. ``close()`` is idempotent.
3. An injected client is NEVER closed by ``close()`` — the caller owns
   that lifecycle.
4. The context-manager protocol calls ``close()`` on exit.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from magi_agent.web_acquisition.providers.jina_reader import JinaReaderProvider


def test_close_closes_owned_default_client() -> None:
    provider = JinaReaderProvider()
    assert provider._owns_client is True
    assert provider._client.is_closed is False
    provider.close()
    assert provider._client.is_closed is True


def test_close_is_idempotent() -> None:
    provider = JinaReaderProvider()
    provider.close()
    # Second call must not raise even though the client is already closed.
    provider.close()
    assert provider._client.is_closed is True


def test_close_does_not_close_injected_client() -> None:
    injected = MagicMock(spec=httpx.Client)
    provider = JinaReaderProvider(client=injected)
    assert provider._owns_client is False
    provider.close()
    injected.close.assert_not_called()


def test_context_manager_closes_on_exit() -> None:
    with JinaReaderProvider() as provider:
        owned_client = provider._client
        assert owned_client.is_closed is False
    assert owned_client.is_closed is True


def test_context_manager_with_injected_client_leaves_it_open() -> None:
    injected = MagicMock(spec=httpx.Client)
    with JinaReaderProvider(client=injected):
        pass
    injected.close.assert_not_called()


def test_close_failure_is_swallowed() -> None:
    """``close()`` must be best-effort — never raise from cleanup."""

    provider = JinaReaderProvider()
    # Patch the client's ``close`` to raise; the provider's ``close``
    # should still complete without re-raising.
    provider._client.close = MagicMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    provider.close()  # must not raise
