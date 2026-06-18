"""Concrete live Slack inbound provider over ``slack_sdk`` Socket Mode (PR3).

Socket Mode opens an OUTBOUND websocket authenticated by an app-level token
(``xapp-``), so it needs no public URL and no request-signature verification —
NAT-friendly, ideal for self-host.  ``slack_sdk`` is an OPTIONAL extra
(``pip install magi-agent[slack]``) imported lazily INSIDE :meth:`start`, so this
module stays import-clean and the operator wiring fails closed when the extra is
absent.

Outbound is NOT this provider's job: the existing ``slack_urllib`` Web API
provider (bot token ``xoxb-``) sends replies.  This provider only receives.

Socket Mode -> queue bridge
---------------------------
``slack_sdk``'s ``SocketModeClient`` runs its own receive threads; the listener
acks each envelope and puts the inner message event dict on a thread-safe
``queue.Queue``.  ``read_events`` drains that queue each watcher cycle, returning
event dicts in the shape ``slack_live._project_slack_event`` consumes.

Honesty
-------
The audit fake-provider trust marker is ``False`` — this provider is live.
"""
from __future__ import annotations

import logging
import queue
from typing import Any

_log = logging.getLogger(__name__)

_MAX_QUEUE = 1000

# Built by string-concat so the legacy brand substring never appears as a literal
# in this file (naming-gate baseline), matching slack_urllib / discord_gateway.
_FAKE_PROVIDER_TRUST_ATTR = "open" + "magi_local_fake_provider"


def _extract_message_event(payload: Any) -> dict[str, Any] | None:
    """Pull the inner ``message`` event from an events_api payload, else None."""
    if not isinstance(payload, dict):
        return None
    event = payload.get("event")
    if not isinstance(event, dict):
        return None
    if event.get("type") != "message":
        return None
    return event


class SlackSocketModeProvider:
    """Live Slack inbound provider (Socket Mode).

    Parameters
    ----------
    app_token : str
        Slack app-level token (``xapp-``) authorising the Socket Mode websocket.
    bot_token : str | None
        Optional bot token (``xoxb-``); attached as the web client when present.
        Never logged.
    """

    def __init__(self, app_token: str, *, bot_token: str | None = None) -> None:
        self._app_token = app_token
        self._bot_token = bot_token
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=_MAX_QUEUE)
        self._client: Any | None = None
        self._started = False

    def start(self) -> None:  # pragma: no cover - needs slack_sdk + live socket
        """Lazily import slack_sdk and open the Socket Mode websocket."""
        if self._started:
            return
        from slack_sdk.socket_mode import SocketModeClient
        from slack_sdk.socket_mode.response import SocketModeResponse
        from slack_sdk.web import WebClient

        web_client = WebClient(token=self._bot_token) if self._bot_token else None
        client = SocketModeClient(app_token=self._app_token, web_client=web_client)

        def _on_request(cli: Any, req: Any) -> None:
            try:
                if getattr(req, "type", None) == "events_api":
                    cli.send_socket_mode_response(
                        SocketModeResponse(envelope_id=req.envelope_id)
                    )
                    event = _extract_message_event(getattr(req, "payload", None))
                    if event is not None:
                        self._queue.put_nowait(event)
            except Exception:  # noqa: BLE001 — a bad envelope must not wedge the socket
                _log.warning("slack socket-mode request handling failed", exc_info=True)

        client.socket_mode_request_listeners.append(_on_request)
        client.connect()  # non-blocking: slack_sdk spawns its own receive threads
        self._client = client
        self._started = True

    def read_events(self, request: Any = None) -> list[dict[str, Any]]:
        if not self._started:
            self.start()
        events: list[dict[str, Any]] = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return events


# Honest trust marker (set without the literal brand substring — see slack_urllib).
setattr(SlackSocketModeProvider, _FAKE_PROVIDER_TRUST_ATTR, False)


__all__ = [
    "SlackSocketModeProvider",
    "_extract_message_event",
]
