from __future__ import annotations

from collections.abc import Callable

__all__ = ["health_payload", "healthz_payload"]

_HEALTH_EXPORTS = frozenset(__all__)


def __getattr__(name: str) -> Callable[..., dict[str, object]]:
    if name not in _HEALTH_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from .health import health_payload, healthz_payload

    exports: dict[str, Callable[..., dict[str, object]]] = {
        "health_payload": health_payload,
        "healthz_payload": healthz_payload,
    }
    value = exports[name]
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | _HEALTH_EXPORTS)
