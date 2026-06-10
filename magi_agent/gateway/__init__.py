"""Track F — the ``magi gateway`` always-on daemon package.

Composes the existing always-on building blocks into one supervised process:
  - the scheduler cron ticker (``harness.scheduler_loop_driver``)
  - the per-platform channel poll loops (``channels.*_live``)
  - session-expiry / platform-reconnect watchers

Everything is default-OFF: the daemon does nothing unless
``MAGI_GATEWAY_DAEMON_ENABLED`` is truthy, and each watcher additionally
respects its own gate.  Nothing here constructs a real network client or calls
``uvicorn.run`` — the run-loop is awaitable and driven by an injected
``stop_event`` so tests can supervise fake watchers.
"""
from __future__ import annotations

__all__ = ["daemon", "pidfile", "service_install", "watchers"]
