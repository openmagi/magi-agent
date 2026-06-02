from __future__ import annotations

from dataclasses import dataclass

from magi_agent.hooks.manifest import HookManifest, HookPoint


_NON_USER_UNREGISTERABLE_SOURCES = {"builtin", "native-plugin"}


@dataclass(frozen=True)
class HookRegistration:
    manifest: HookManifest
    enabled: bool
    protected: bool
    unregister_protected: bool


class HookRegistry:
    def __init__(self) -> None:
        self._hooks: dict[str, HookRegistration] = {}

    def register(self, manifest: HookManifest) -> None:
        if manifest.name in self._hooks:
            raise ValueError(f"hook already registered: {manifest.name}")
        stored_manifest = _copy_manifest(manifest)
        protected = is_protected_manifest(stored_manifest)
        self._hooks[manifest.name] = HookRegistration(
            manifest=stored_manifest,
            enabled=True if protected else stored_manifest.enabled,
            protected=protected,
            unregister_protected=is_unregister_protected_manifest(stored_manifest),
        )

    def replace(self, manifest: HookManifest) -> None:
        existing = self._hooks.get(manifest.name)
        stored_manifest = _copy_manifest(manifest)
        if existing and existing.protected:
            stored_manifest = _with_preserved_protected_metadata(
                existing.manifest,
                stored_manifest,
            )
        protected = (existing.protected if existing else False) or is_protected_manifest(
            stored_manifest
        )
        if protected:
            enabled = True
        elif existing:
            enabled = existing.enabled
        else:
            enabled = stored_manifest.enabled
        self._hooks[manifest.name] = HookRegistration(
            manifest=stored_manifest,
            enabled=enabled,
            protected=protected,
            unregister_protected=(existing.unregister_protected if existing else False)
            or is_unregister_protected_manifest(stored_manifest),
        )

    def unregister(self, name: str) -> HookManifest:
        registration = self._hooks.get(name)
        if registration is None:
            raise KeyError(name)
        if registration.unregister_protected:
            raise ValueError(f"cannot unregister protected hook: {name}")
        del self._hooks[name]
        return _copy_registration_manifest(registration)

    def resolve(self, name: str) -> HookManifest | None:
        registration = self._hooks.get(name)
        return _copy_registration_manifest(registration) if registration else None

    def enable(self, name: str) -> None:
        registration = self._hooks[name]
        self._hooks[name] = HookRegistration(
            manifest=registration.manifest,
            enabled=True,
            protected=registration.protected,
            unregister_protected=registration.unregister_protected,
        )

    def disable(self, name: str) -> None:
        registration = self._hooks[name]
        if registration.protected:
            raise ValueError(f"cannot disable protected hook: {name}")
        self._hooks[name] = HookRegistration(
            manifest=registration.manifest,
            enabled=False,
            protected=registration.protected,
            unregister_protected=registration.unregister_protected,
        )

    def list_all(self) -> list[HookManifest]:
        return [
            _copy_registration_manifest(registration)
            for registration in self._sorted_registrations()
        ]

    def list_enabled(self, point: HookPoint) -> list[HookManifest]:
        return [
            _copy_registration_manifest(registration)
            for registration in self._point_registrations(point)
            if registration.enabled
        ]

    def stats(self) -> dict[str, dict[str, int]]:
        return {name: _zero_stats() for name in sorted(self._hooks)}

    def _sorted_registrations(self) -> list[HookRegistration]:
        return [self._hooks[name] for name in sorted(self._hooks)]

    def _point_registrations(self, point: HookPoint) -> list[HookRegistration]:
        return sorted(
            (
                registration
                for registration in self._hooks.values()
                if registration.manifest.point is point
            ),
            key=lambda registration: (
                registration.manifest.priority,
                registration.manifest.name,
            ),
        )


def is_protected_manifest(manifest: HookManifest) -> bool:
    return (
        manifest.security_critical
        or manifest.scope.hard_safety
        or not manifest.opt_out
    )


def is_unregister_protected_manifest(manifest: HookManifest) -> bool:
    return is_protected_manifest(manifest) or _is_non_user_unregisterable(manifest)


def _is_non_user_unregisterable(manifest: HookManifest) -> bool:
    return manifest.source.kind in _NON_USER_UNREGISTERABLE_SOURCES


def _copy_manifest(manifest: HookManifest) -> HookManifest:
    return manifest.model_copy(deep=True)


def _with_preserved_protected_metadata(
    existing: HookManifest,
    replacement: HookManifest,
) -> HookManifest:
    preserved_scope = existing.scope.model_copy(
        deep=True,
        update={
            "hard_safety": existing.scope.hard_safety
            or replacement.scope.hard_safety,
        },
    )
    return replacement.model_copy(
        deep=True,
        update={
            "point": existing.point,
            "priority": min(existing.priority, replacement.priority),
            "blocking": existing.blocking or replacement.blocking,
            "fail_open": existing.fail_open and replacement.fail_open,
            "timeout_ms": min(existing.timeout_ms, replacement.timeout_ms),
            "scope": preserved_scope,
            "security_critical": existing.security_critical
            or replacement.security_critical,
            "opt_out": existing.opt_out and replacement.opt_out,
        },
    )


def _copy_registration_manifest(registration: HookRegistration) -> HookManifest:
    return registration.manifest.model_copy(
        deep=True,
        update={"enabled": registration.enabled},
    )


def _zero_stats() -> dict[str, int]:
    return {
        "totalRuns": 0,
        "timeouts": 0,
        "errors": 0,
        "blocks": 0,
        "avgDurationMs": 0,
        "lastRunAt": 0,
    }


__all__ = [
    "HookRegistration",
    "HookRegistry",
    "is_protected_manifest",
    "is_unregister_protected_manifest",
]
