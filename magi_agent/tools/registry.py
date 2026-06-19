from __future__ import annotations

from .base import ToolHandler, ToolRegistration
from .manifest import RuntimeMode, ToolManifest


_APPROVAL_PERMISSION_CLASSES = {"write", "execute", "net", "computer"}
_APPROVAL_TAGS = {"requires-approval", "approval-required"}
_BUDGET_LIMIT_FIELDS = (
    "max_calls_per_turn",
    "max_parallel",
    "output_chars",
    "transcript_chars",
)
_SIDE_EFFECT_STRENGTH = {
    "none": 0,
    "local_process": 1,
    "local_workspace": 2,
    "external": 3,
    "local_and_external": 4,
}
_PARALLEL_SAFETY_STRENGTH = {
    "unsafe": 0,
    "readonly": 1,
    "concurrency_safe": 2,
}
_LATENCY_STRENGTH = {
    "inline": 0,
    "interactive": 1,
    "background": 2,
    "long_running": 3,
}
_COST_STRENGTH = {
    "free": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "metered": 4,
}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolRegistration] = {}

    def register(self, manifest: ToolManifest, *, handler: ToolHandler | None = None) -> None:
        if manifest.name in self._tools:
            raise ValueError(f"tool already registered: {manifest.name}")
        stored_manifest = _copy_manifest(manifest)
        self._tools[manifest.name] = ToolRegistration(
            manifest=stored_manifest,
            handler=handler,
            enabled=stored_manifest.enabled_by_default,
            protected=is_protected_manifest(stored_manifest),
        )

    def replace(self, manifest: ToolManifest, *, handler: ToolHandler | None = None) -> None:
        existing = self._tools.get(manifest.name)
        stored_manifest = _copy_manifest(manifest)
        if existing is not None and existing.protected:
            downgrade_reasons = _protected_metadata_downgrade_reasons(
                existing.manifest,
                stored_manifest,
            )
            if downgrade_reasons:
                fields = ", ".join(downgrade_reasons)
                raise ValueError(f"cannot downgrade protected tool metadata: {fields}")
        self._tools[manifest.name] = ToolRegistration(
            manifest=stored_manifest,
            handler=_replacement_handler(existing, handler),
            enabled=existing.enabled if existing else stored_manifest.enabled_by_default,
            protected=(existing.protected if existing else False) or is_protected_manifest(stored_manifest),
        )

    def bind_handler(
        self,
        name: str,
        handler: ToolHandler,
        *,
        enabled_by_registry_policy: bool = False,
        manifest: ToolManifest | None = None,
    ) -> None:
        if manifest is not None:
            raise ValueError("bind_handler does not accept manifest replacements")
        registration = self._tools.get(name)
        if registration is None:
            raise KeyError(name)
        if (
            registration.protected
            and registration.handler is not None
            and registration.handler is not handler
        ):
            raise ValueError(f"protected tool handler already bound: {name}")
        self._tools[name] = ToolRegistration(
            manifest=registration.manifest,
            handler=handler,
            enabled=True if enabled_by_registry_policy else registration.enabled,
            protected=registration.protected,
        )

    def unregister(self, name: str) -> ToolManifest:
        registration = self._tools.get(name)
        if registration is None:
            raise KeyError(name)
        manifest = registration.manifest
        if registration.protected:
            raise ValueError(f"cannot unregister core/builtin tool: {name}")
        del self._tools[name]
        return _copy_manifest(manifest)

    def resolve(self, name: str) -> ToolManifest | None:
        registration = self._tools.get(name)
        return _copy_manifest(registration.manifest) if registration else None

    def resolve_enabled(self, name: str) -> ToolManifest | None:
        registration = self._tools.get(name)
        if registration is None or not registration.enabled:
            return None
        return _copy_manifest(registration.manifest)

    def resolve_registration(self, name: str) -> ToolRegistration | None:
        registration = self._tools.get(name)
        return _copy_registration(registration) if registration else None

    def enable(self, name: str) -> None:
        registration = self._tools[name]
        self._tools[name] = ToolRegistration(
            manifest=registration.manifest,
            handler=registration.handler,
            enabled=True,
            protected=registration.protected,
        )

    def disable(self, name: str) -> None:
        registration = self._tools[name]
        self._tools[name] = ToolRegistration(
            manifest=registration.manifest,
            handler=registration.handler,
            enabled=False,
            protected=registration.protected,
        )

    def is_enabled(self, name: str) -> bool:
        registration = self._tools.get(name)
        return bool(registration and registration.enabled)

    def list_available(self, *, mode: RuntimeMode) -> list[ToolManifest]:
        return [
            _copy_manifest(registration.manifest)
            for registration in self._sorted_registrations()
            if registration.enabled and mode in registration.manifest.available_in_modes
        ]

    def list_all(self) -> list[ToolManifest]:
        return [
            _copy_manifest(registration.manifest)
            for registration in self._sorted_registrations()
        ]

    def _sorted_registrations(self) -> list[ToolRegistration]:
        return [self._tools[name] for name in sorted(self._tools)]


def is_protected_manifest(manifest: ToolManifest) -> bool:
    return manifest.kind == "core" or manifest.source.kind == "builtin"


def _protected_metadata_downgrade_reasons(
    existing: ToolManifest,
    replacement: ToolManifest,
) -> list[str]:
    reasons: list[str] = []

    if existing.kind == "core" and replacement.kind != "core":
        reasons.append("kind")
    if existing.source.kind == "builtin" and replacement.source != existing.source:
        reasons.append("source")
    if _permission_downgraded(existing.permission, replacement.permission):
        reasons.append("permission")
    if existing.dangerous and not replacement.dangerous:
        reasons.append("dangerous")
    if existing.mutates_workspace and not replacement.mutates_workspace:
        reasons.append("mutatesWorkspace")
    if not set(replacement.available_in_modes).issubset(existing.available_in_modes):
        reasons.append("availableInModes")
    if _approval_tags(existing).difference(replacement.tags):
        reasons.append("tags")
    if existing.should_defer and not replacement.should_defer:
        reasons.append("shouldDefer")
    if not existing.is_concurrency_safe and replacement.is_concurrency_safe:
        reasons.append("isConcurrencySafe")
    if _rank_downgraded(
        existing.side_effect_class,
        replacement.side_effect_class,
        _SIDE_EFFECT_STRENGTH,
    ):
        reasons.append("sideEffectClass")
    if _rank_downgraded(
        existing.parallel_safety,
        replacement.parallel_safety,
        _PARALLEL_SAFETY_STRENGTH,
    ):
        reasons.append("parallelSafety")
    elif _rank_overclaimed(
        existing.parallel_safety,
        replacement.parallel_safety,
        _PARALLEL_SAFETY_STRENGTH,
    ):
        reasons.append("parallelSafety")
    if _metadata_changed(existing.emits_evidence_types, replacement.emits_evidence_types):
        reasons.append("emitsEvidenceTypes")
    if _metadata_changed(
        existing.deterministic_requirement_types,
        replacement.deterministic_requirement_types,
    ):
        reasons.append("deterministicRequirementTypes")
    if (
        existing.can_satisfy_deterministic_requirement
        and not replacement.can_satisfy_deterministic_requirement
    ):
        reasons.append("canSatisfyDeterministicRequirement")
    elif (
        not existing.can_satisfy_deterministic_requirement
        and replacement.can_satisfy_deterministic_requirement
    ):
        reasons.append("canSatisfyDeterministicRequirement")
    if existing.adk_tool_type != replacement.adk_tool_type:
        reasons.append("adkToolType")
    if _rank_downgraded(existing.latency_class, replacement.latency_class, _LATENCY_STRENGTH):
        reasons.append("latencyClass")
    if _rank_downgraded(existing.cost_class, replacement.cost_class, _COST_STRENGTH):
        reasons.append("costClass")
    if _metadata_changed(existing.capability_tags, replacement.capability_tags):
        reasons.append("capabilityTags")
    if _drops_metadata(existing.preconditions, replacement.preconditions):
        reasons.append("preconditions")
    if _drops_metadata(existing.postconditions, replacement.postconditions):
        reasons.append("postconditions")
    if _drops_metadata(
        existing.transient_failure_classes,
        replacement.transient_failure_classes,
    ):
        reasons.append("transientFailureClasses")
    if replacement.timeout_ms > existing.timeout_ms:
        reasons.append("timeoutMs")
    for field in _BUDGET_LIMIT_FIELDS:
        if _limit_loosened(
            getattr(existing.budget, field),
            getattr(replacement.budget, field),
        ):
            reasons.append(f"budget.{field}")

    return reasons


def _permission_downgraded(existing: str, replacement: str) -> bool:
    if existing in _APPROVAL_PERMISSION_CLASSES:
        return replacement != existing
    return replacement not in _APPROVAL_PERMISSION_CLASSES and replacement != existing


def _approval_tags(manifest: ToolManifest) -> set[str]:
    return _APPROVAL_TAGS.intersection(manifest.tags)


def _replacement_handler(
    existing: ToolRegistration | None,
    replacement: ToolHandler | None,
) -> ToolHandler | None:
    if existing is None:
        return replacement
    if existing.protected:
        return existing.handler
    return replacement if replacement is not None else existing.handler


def _limit_loosened(existing: int | None, replacement: int | None) -> bool:
    if existing is None:
        return False
    if replacement is None:
        return True
    return replacement > existing


def _rank_downgraded(
    existing: str,
    replacement: str,
    strength_by_value: dict[str, int],
) -> bool:
    return strength_by_value[replacement] < strength_by_value[existing]


def _rank_overclaimed(
    existing: str,
    replacement: str,
    strength_by_value: dict[str, int],
) -> bool:
    return strength_by_value[replacement] > strength_by_value[existing]


def _drops_metadata(existing: tuple[str, ...], replacement: tuple[str, ...]) -> bool:
    return not set(existing).issubset(replacement)


def _metadata_changed(existing: tuple[str, ...], replacement: tuple[str, ...]) -> bool:
    return set(existing) != set(replacement)


def _copy_manifest(manifest: ToolManifest) -> ToolManifest:
    return manifest.model_copy(deep=True)


def _copy_registration(registration: ToolRegistration) -> ToolRegistration:
    return ToolRegistration(
        manifest=_copy_manifest(registration.manifest),
        handler=registration.handler,
        enabled=registration.enabled,
        protected=registration.protected,
    )
