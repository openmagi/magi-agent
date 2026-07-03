from __future__ import annotations

import asyncio
import logging
from typing import Any

from magi_agent.observability.api import build_api_router
from magi_agent.observability.bus import ActivityBus
from magi_agent.observability.config import ObservabilityConfig
from magi_agent.observability.projector import project, project_public_event
from magi_agent.observability.store import ActivityStore
from magi_agent.observability.taxonomy import NOISE_KINDS

logger = logging.getLogger(__name__)

_RETENTION_INTERVAL_S = 3600


class ObservabilityCore:
    """Assembles store + bus + router behind the config flag. No app wiring,
    no hook registration — those are the second plan. `record_from_hook` is the
    fail-open entry point the future tap will call."""

    def __init__(self, config: ObservabilityConfig, *, runtime: Any) -> None:
        self.config = config
        self.store: ActivityStore | None = None
        self.bus: ActivityBus | None = None
        self.router = None
        self._retention_started = False
        if not config.enabled:
            return
        # Pass NOISE_KINDS so the store commit-batches high-volume noise inserts
        # and prunes them ahead of enforcement events (PR-D4 / N-16).
        self.store = ActivityStore(config.db_path, noise_kinds=tuple(NOISE_KINDS))
        self.bus = ActivityBus(replay=config.replay_buffer)
        self.router = build_api_router(self.store, self.bus, runtime)

    def ensure_retention_started(self) -> None:
        if self.store is None or self._retention_started:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no running loop yet; will start on a later in-loop call
        self._retention_started = True
        loop.create_task(self._retention_loop())

    async def _retention_loop(self) -> None:
        while True:
            await asyncio.sleep(_RETENTION_INTERVAL_S)
            try:
                if self.store is not None:
                    self.store.prune(
                        max_events=self.config.max_events,
                        retention_days=self.config.retention_days,
                        noise_kinds=tuple(NOISE_KINDS),
                    )
            except Exception:
                logger.debug("observability retention prune failed", exc_info=True)

    def record_public_event(
        self, payload: dict, session_id: str | None, turn_id: str | None
    ) -> None:
        if not self.config.enabled or self.store is None:
            return
        self.ensure_retention_started()
        try:
            event = project_public_event(payload, session_id=session_id, turn_id=turn_id)
            if event is None:
                return
            self.store.record_event(event)
            if self.bus is not None:
                self._publish(event.model_dump())
        except Exception:
            logger.debug("observability record_public_event failed", exc_info=True)

    def record_from_hook(self, point: str, ctx: Any) -> None:
        if not self.config.enabled or self.store is None:
            return
        try:
            event = project(point, ctx)
            if event is None:
                return
            self.store.record_event(event)
            if self.bus is not None:
                self._publish(event.model_dump())
        except Exception:  # fail-open: visibility must never break the agent
            logger.debug("observability record_from_hook failed", exc_info=True)

    def _publish(self, payload: dict) -> None:
        if self.bus is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self.bus.publish(payload))
        task.add_done_callback(self._log_publish_result)

    @staticmethod
    def _log_publish_result(task: "asyncio.Task") -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.debug("observability bus publish failed", exc_info=exc)

    def close(self) -> None:
        if self.store is not None:
            self.store.close()
