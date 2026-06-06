"""Suppress a benign OpenTelemetry teardown log line.

ADK/litellm spans are attached and detached across asyncio task boundaries. When
a turn unwinds, OpenTelemetry can try to detach a context token from a different
execution context than the one it was created in, and logs a full
``logger.exception("Failed to detach context")`` traceback from
``opentelemetry.context``. This is harmless teardown noise, but it floods the
CLI/serve stderr and looks like a crash to users.

We install a narrow logging filter that drops only that specific record on the
``opentelemetry.context`` logger. Every other OpenTelemetry log is left intact.
"""

from __future__ import annotations

import logging

_FILTER_FLAG = "_magi_detach_noise_filter"
_TARGET_LOGGER = "opentelemetry.context"
_TARGET_MESSAGE = "Failed to detach context"


class _DetachNoiseFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage() != _TARGET_MESSAGE


def silence_otel_detach_noise() -> None:
    """Idempotently drop the ``Failed to detach context`` log record."""

    logger = logging.getLogger(_TARGET_LOGGER)
    if getattr(logger, _FILTER_FLAG, False):
        return
    logger.addFilter(_DetachNoiseFilter())
    setattr(logger, _FILTER_FLAG, True)


__all__ = ["silence_otel_detach_noise"]
