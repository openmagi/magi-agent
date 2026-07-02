from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Literal, NamedTuple, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from magi_agent.ops.safety import contains_secret_marker


ModelTier: TypeAlias = Literal[
    "cheap",
    "standard",
    "sota",
    "reasoning",
    "long_context",
    "vision",
    "local",
]
ModelCapability: TypeAlias = Literal[
    "tool_use",
    "function_calling",
    "json_schema",
    "streaming",
    "long_context",
    "coding",
    "reasoning",
    "citation_grounding",
    "vision",
    "low_latency",
]
ModelUsagePhase: TypeAlias = Literal[
    "intent_classification",
    "planning",
    "source_acquisition",
    "source_extraction",
    "code_search",
    "patch_planning",
    "patch_generation",
    "test_interpretation",
    "final_answer_drafting",
    "final_verification",
    "high_risk_review",
]


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_PROVIDER_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_MODEL_RE = re.compile(r"^(?=.{1,128}$)[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*){0,5}$")
# C-12: single label-shape regex. The pre-C-12 module shipped two byte-identical
# regexes that differed only by including ``/`` in the rejected char class
# (``_UNSAFE_LABEL_RE`` rejected ``/``; ``_UNSAFE_MODEL_LABEL_RE`` allowed it).
# This unified regex drops ``/`` from the char class (allowing model paths like
# ``provider/family/variant``); the provider/capability paths still reject
# slashes via their secondary ``_PROVIDER_RE.fullmatch`` shape constraint (or by
# being filtered out of the cleaned list silently). The generic
# Bearer/sk-/xox-/gh*_/AIza/api_key/secret/token/password/private_key
# alternations that previously lived here are now delegated to the C-1 redaction
# kernel via :func:`magi_agent.ops.safety.contains_secret_marker` — a strict
# superset (and the single source of truth for the secret-vocabulary).
_UNSAFE_LABEL_RE = re.compile(
    r"(?:"
    r"^\s*$|"
    r"\s|"
    r"[\\'\"`$=;|&<>]|"
    r"\.\.|"
    r"~|"
    r"://"
    r")",
    re.IGNORECASE,
)


class _StrictModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        return cls(**values)


class ResolvedModelTier(_StrictModel):
    provider: str
    model: str
    tier: ModelTier
    capabilities: tuple[ModelCapability, ...] = ()
    dropped_requested_capabilities: tuple[str, ...] = Field(
        default=(),
        alias="droppedRequestedCapabilities",
    )
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")


class ModelTierPolicy(_StrictModel):
    recipe_id: str = Field(alias="recipeId")
    phase: ModelUsagePhase
    minimum_tier: ModelTier = Field(default="standard", alias="minimumTier")
    preferred_tier: ModelTier = Field(default="standard", alias="preferredTier")
    sota_reason: str | None = Field(default=None, alias="sotaReason")

    @model_validator(mode="after")
    def _validate_sota_reason(self) -> Self:
        if self.minimum_tier == "sota" and not (self.sota_reason or "").strip():
            raise ValueError("sotaReason is required when minimumTier is sota")
        return self


class _ModelTierRecord(_StrictModel):
    provider: str
    model: str
    tier: ModelTier
    capabilities: tuple[ModelCapability, ...] = ()

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, value: str) -> str:
        return _validate_provider(value)

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str) -> str:
        return _validate_model(value)


class _ResolveRequest(_StrictModel):
    provider: str
    model: str
    requested_capabilities: tuple[str, ...] = Field(
        default=(),
        alias="requestedCapabilities",
    )

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, value: str) -> str:
        return _validate_provider(value)

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str) -> str:
        return _validate_model(value)

    @field_validator("requested_capabilities")
    @classmethod
    def _validate_requested_capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        clean: list[str] = []
        for item in value:
            text = str(item).strip()
            if (
                not text
                or "/" in text
                or _UNSAFE_LABEL_RE.search(text)
                or contains_secret_marker(text)
            ):
                # C-12: capability labels are simple identifiers (no slash, no
                # secret-shape). The C-1 kernel covers the secret vocabulary
                # uniformly; the explicit "/" reject was previously implicit in
                # the now-folded ``_UNSAFE_LABEL_RE`` char class.
                continue
            clean.append(text)
        return tuple(dict.fromkeys(clean))


class ModelTierRegistry:
    def __init__(self, records: tuple[_ModelTierRecord, ...]) -> None:
        self._records = {
            (record.provider, record.model): record
            for record in sorted(records, key=lambda item: (item.provider, item.model))
        }

    @classmethod
    def with_defaults(cls) -> Self:
        """Build the default registry from the single ``ModelCatalog`` (E-1).

        Delegates to :meth:`from_catalog` so the historic hand-written
        9-record block lives in one place (``models/builtin_catalog.json``)
        and the gemini canonical record fans out to BOTH ``gemini`` and
        ``google`` registry labels via the catalog's ``provider_aliases`` map.
        Subset preserved byte-identically: every (provider, model) pair
        ``resolve_child_route`` accepted before E-1 still resolves here.
        """
        from magi_agent.models.catalog import ModelCatalog  # noqa: PLC0415

        return cls.from_catalog(ModelCatalog.builtin())

    @classmethod
    def from_catalog(cls, catalog: object) -> Self:
        """Build a tier registry from a :class:`magi_agent.models.ModelCatalog`.

        For every catalog record with a non-empty ``capabilities`` tuple (the
        sota/cheap registry-eligible ones), emit a tier record under the
        canonical provider AND under every alias mapping to that canonical
        provider (e.g. ``google`` → ``gemini``). The catalog stores each
        provider/model exactly once; aliases are applied here so the resulting
        registry is byte-equivalent to the pre-E-1 ``with_defaults`` output.
        """
        # Late import: catalog itself imports ModelCapability/ModelTier from
        # this module, so the runtime dep is one-way (catalog → model_tiers).
        # Loading the catalog here is safe — by the time ``with_defaults`` is
        # called, the typing aliases have long since been imported.
        records: list[_ModelTierRecord] = []
        aliases = catalog.provider_aliases()  # type: ignore[attr-defined]
        # Reverse map: canonical → set of aliases (including itself).
        canonical_to_labels: dict[str, set[str]] = {}
        for alias, canonical in aliases.items():
            canonical_to_labels.setdefault(canonical, set()).add(alias)
        for r in catalog.all_records():  # type: ignore[attr-defined]
            if not r.capabilities:
                continue  # router/product entries with no tier metadata.
            labels = {r.provider}
            labels |= canonical_to_labels.get(r.provider, set())
            for label in labels:
                records.append(
                    _ModelTierRecord(
                        provider=label,
                        model=r.model,
                        tier=r.tier,
                        capabilities=r.capabilities,
                    )
                )
        return cls(records=tuple(records))

    @classmethod
    def from_records(cls, records: tuple[Mapping[str, object], ...]) -> Self:
        return cls(tuple(_ModelTierRecord.model_validate(record) for record in records))

    def resolve(
        self,
        *,
        provider: str,
        model: str,
        requestedCapabilities: tuple[str, ...] = (),
        requested_capabilities: tuple[str, ...] = (),
    ) -> ResolvedModelTier:
        request = _ResolveRequest(
            provider=provider,
            model=model,
            requestedCapabilities=requestedCapabilities or requested_capabilities,
        )
        record = self._records.get((request.provider, request.model))
        if record is None:
            return ResolvedModelTier(
                provider=request.provider,
                model=request.model,
                tier="standard",
                capabilities=(),
                droppedRequestedCapabilities=request.requested_capabilities,
                reasonCodes=("unknown_model_standard_no_elevated_capabilities",),
            )

        capabilities = record.capabilities
        dropped = tuple(
            capability
            for capability in request.requested_capabilities
            if capability not in capabilities
        )
        return ResolvedModelTier(
            provider=record.provider,
            model=record.model,
            tier=record.tier,
            capabilities=capabilities,
            droppedRequestedCapabilities=dropped,
        )


def _validate_provider(value: str) -> str:
    clean = value.strip().casefold()
    # C-12: ``_PROVIDER_RE = ^[a-z][a-z0-9-]{0,31}$`` already rejects ``/``,
    # subsuming the pre-C-12 char-class slash reject from the now-folded
    # ``_UNSAFE_LABEL_RE``. The C-1 kernel covers the secret vocabulary.
    if (
        _UNSAFE_LABEL_RE.search(clean)
        or contains_secret_marker(clean)
        or not _PROVIDER_RE.fullmatch(clean)
    ):
        raise ValueError("provider label must be a safe server-side provider label")
    return clean


def _validate_model(value: str) -> str:
    clean = value.strip().casefold()
    # C-12: ``_MODEL_RE`` allows ``/`` (multi-segment model IDs like
    # ``openai/gpt-4o``), so the unified ``_UNSAFE_LABEL_RE`` drops ``/`` from
    # its char class to avoid false-rejecting valid model labels. The C-1
    # kernel covers the secret vocabulary uniformly with the provider path.
    if (
        _UNSAFE_LABEL_RE.search(clean)
        or contains_secret_marker(clean)
        or not _MODEL_RE.fullmatch(clean)
    ):
        raise ValueError("model label must be a safe server-side model label")
    return clean


class ChildRoute(NamedTuple):
    """A validated child-spawn route (canonical ``provider``/``model``)."""

    provider: str
    model: str


# Registry provider labels for the gemini/google dual-alias pair (and any
# future CLI/registry alias the catalog declares).  Derived lazily from
# ``ModelCatalog.provider_aliases`` (E-1): for every ``alias -> canonical`` pair
# in the catalog, the CLI-side ``canonical`` covers BOTH labels in the registry.
#
# Lazy population avoids a circular import: ``models.types`` imports the typing
# aliases from THIS module, so this module must finish its body before the
# catalog can be loaded.  Callers access ``_PROVIDER_REGISTRY_ALIASES.get(...)``
# (uniformly) so the proxy below is API-compatible with the legacy dict.
class _LazyAliasMap(dict):
    """A dict that populates itself from the ModelCatalog on first access."""

    _initialised = False

    def _populate(self) -> None:
        if self._initialised:
            return
        # Set the sentinel BEFORE the catalog load so any re-entrant access
        # (a callback hit during catalog init, etc.) short-circuits cleanly.
        self._initialised = True
        try:
            from magi_agent.models.catalog import ModelCatalog  # noqa: PLC0415

            catalog = ModelCatalog.builtin()
            aliases = catalog.provider_aliases()
        except Exception:  # noqa: BLE001 — fail-soft to empty alias map
            return
        out: dict[str, set[str]] = {}
        for alias, canonical in aliases.items():
            bucket = out.setdefault(canonical, {canonical})
            bucket.add(alias)
        for k, v in out.items():
            dict.__setitem__(self, k, frozenset(v))

    def get(self, key, default=None):  # type: ignore[override]
        self._populate()
        return dict.get(self, key, default)

    def __getitem__(self, key):  # type: ignore[override]
        self._populate()
        return dict.__getitem__(self, key)

    def __contains__(self, key) -> bool:  # type: ignore[override]
        self._populate()
        return dict.__contains__(self, key)

    def items(self):  # type: ignore[override]
        self._populate()
        return dict.items(self)

    def keys(self):  # type: ignore[override]
        self._populate()
        return dict.keys(self)

    def values(self):  # type: ignore[override]
        self._populate()
        return dict.values(self)


_PROVIDER_REGISTRY_ALIASES: dict[str, frozenset[str]] = _LazyAliasMap()


def _empty_debug_enabled_local(env: Mapping[str, str]) -> bool:
    """Local mirror of ``child_runner_live._empty_debug_enabled``.

    Mirrors the parse to avoid a circular import (``child_runner_live`` already
    imports from this module). The two predicates must accept the same
    truthiness set: ``1``/``true``/``yes``/``on`` (case-insensitive).
    """
    # I-1: route through the typed flag registry. The ``FlagSpec`` is
    # registered as a strict default-OFF ``_b(...)``; ``flag_bool``
    # shares the canonical ``env_bool`` truthy parser with the
    # ``{1, true, yes, on}`` set this predicate hand-rolled.
    from magi_agent.config.flags import flag_bool  # noqa: PLC0415

    return flag_bool("MAGI_CHILD_RUNNER_EMPTY_DEBUG", env=env)


def _emit_deprecated_redirect_trace(from_model: str, to_model: str) -> None:
    """Emit the one-line ``deprecated_redirect`` stamp through the trace sink.

    Delegates to the shared :func:`magi_agent.runtime.trace_sink._emit_trace`
    (PR-G) so the line lands in the same file-backed channel as every other
    child-runner / boundary trace. The stamp prefix ``[model_tiers.trace]``
    keeps it greppable separately from the child-runner traces.
    """
    # Imported lazily so importing this module stays light: model_tiers is
    # loaded early by the CLI bootstrap and the sink only matters when the
    # operator opts in via ``MAGI_CHILD_RUNNER_EMPTY_DEBUG``.
    from magi_agent.runtime.trace_sink import _emit_trace  # noqa: PLC0415

    _emit_trace(f"[model_tiers.trace] deprecated_redirect from={from_model} to={to_model}")


def _catalog_deprecation_lookup(provider: str, model: str) -> tuple[bool, str | None]:
    """Return ``(deprecated, replacement)`` for a catalog record.

    Fail-soft: any error (catalog load failure, unknown id, label normalisation
    quirks) returns ``(False, None)`` so the caller treats the route as
    non-deprecated. The lookup honours the catalog's provider-aliases map so
    ``google``/``gemini`` resolve to the same record.

    Surfaced as a module-level function (not inlined) so the redirect test can
    monkeypatch it without rebuilding the whole catalog singleton.
    """
    try:
        from magi_agent.models.catalog import ModelCatalog  # noqa: PLC0415

        catalog = ModelCatalog.builtin()
        record = catalog.record(provider, model)
        if record is None:
            return (False, None)
        return (bool(record.deprecated), record.replacement or None)
    except Exception:  # noqa: BLE001 - fail-soft, never raise.
        return (False, None)


def _keyed_registry_providers(
    env: Mapping[str, str],
) -> set[str] | None:
    """Registry-provider labels whose API key is configured, or ``None`` to mean
    'do not filter' (fail-open: gate OFF, no keys at all, or any error).

    Returns a ``set[str]`` of registry provider labels when gate ON + at least
    one key is found, else ``None``.  Callers treat ``None`` as "skip filtering".
    """
    try:
        from magi_agent.engine.providers import (  # noqa: PLC0415
            configured_providers,
            resolve_provider_config,
        )
        from magi_agent.config.env import (  # noqa: PLC0415
            is_key_aware_model_routes_enabled,
        )

        if not is_key_aware_model_routes_enabled(env):
            return None

        keyed = configured_providers(env=env)
        if not keyed:
            # No keys at all — fail-open to legacy behavior.
            return None

        # Map cli-provider names → registry provider labels.
        registry_set: set[str] = set()
        for p in keyed:
            registry_set |= _PROVIDER_REGISTRY_ALIASES.get(p, frozenset({p}))

        # Also include the *selected* provider's registry label so the configured
        # bot can always route on its own model even if it only has cheap-tier records.
        try:
            sel = resolve_provider_config(env=env)
            if sel:
                registry_set |= _PROVIDER_REGISTRY_ALIASES.get(
                    sel.provider, frozenset({sel.provider})
                )
        except Exception:  # noqa: BLE001 — fail-soft: selected provider is best-effort
            pass

        return registry_set
    except Exception:  # noqa: BLE001 — any error → fail-open (never filter)
        return None


def resolve_child_route(provider: str, model: str, env: Mapping[str, str]) -> ChildRoute | None:
    """Canonical ACCEPTANCE authority for a child-spawn ``(provider, model)``.

    Returns the route a child may run on, else ``None`` (caller blocks). A route
    is accepted iff it (a) resolves in the built-in :class:`ModelTierRegistry`
    without an ``unknown_model_*`` reason code — returned canonical/normalised —
    OR (b) is in the operator's deployment route allowlist — returned as given.

    When ``MAGI_KEY_AWARE_MODEL_ROUTES_ENABLED`` is ON and at least one provider
    key is configured, registry-resolved routes whose provider is NOT in the keyed
    set are treated as not accepted (fall through to allowlist), unless the route
    matches the currently selected provider/model (fail-open for the configured
    bot's own route).

    This is the SINGLE function ``child_runner_live._validate_route`` delegates to
    and that :func:`available_child_model_routes` enumerates against, so the
    routes the model is TOLD about (prompt/tool guidance) can never drift from the
    routes the runner ACCEPTS. Never raises.
    """
    # Compute keyed providers once; None means "do not filter".
    keyed = _keyed_registry_providers(env)

    # Determine the selected provider/model for the fail-open "own route" check.
    sel_provider: str | None = None
    sel_model: str | None = None
    if keyed is not None:
        try:
            from magi_agent.engine.providers import resolve_provider_config  # noqa: PLC0415

            sel = resolve_provider_config(env=env)
            if sel:
                sel_provider = sel.provider.strip().casefold()
                sel_model = sel.model.strip().casefold()
        except Exception:  # noqa: BLE001 — fail-soft
            pass

    try:
        resolved = ModelTierRegistry.with_defaults().resolve(provider=provider, model=model)
    except Exception:  # noqa: BLE001 — label-validation failure → not a registry route.
        resolved = None
    if resolved is not None:
        reason_codes = tuple(getattr(resolved, "reason_codes", ()) or ())
        if not any("unknown_model" in code for code in reason_codes):
            resolved_provider = str(getattr(resolved, "provider", provider))
            resolved_model = str(getattr(resolved, "model", model))
            # PR-4: catalog ``deprecated -> replacement`` auto-redirect.
            #
            # If the resolved (provider, model) is tagged ``deprecated=True``
            # with a non-empty ``replacement`` in builtin_catalog.json, re-resolve
            # against the registry under the replacement id. If the replacement
            # itself does not resolve (catalog authoring bug; unknown id), fall
            # back to the original deprecated route so the caller still gets a
            # usable ChildRoute. Single redirect, no loop: we only consult the
            # deprecation lookup once on the original id, never on the
            # replacement.
            deprecated, replacement = _catalog_deprecation_lookup(resolved_provider, resolved_model)
            if deprecated and replacement:
                try:
                    redirect = ModelTierRegistry.with_defaults().resolve(
                        provider=resolved_provider, model=replacement
                    )
                except Exception:  # noqa: BLE001 - fail-soft.
                    redirect = None
                redirect_codes = tuple(getattr(redirect, "reason_codes", ()) or ())
                if redirect is not None and not any(
                    "unknown_model" in code for code in redirect_codes
                ):
                    redirected_provider = str(getattr(redirect, "provider", resolved_provider))
                    redirected_model = str(getattr(redirect, "model", replacement))
                    if _empty_debug_enabled_local(env):
                        _emit_deprecated_redirect_trace(resolved_model, redirected_model)
                    resolved_provider = redirected_provider
                    resolved_model = redirected_model
                # else: unresolvable replacement, fail-soft to the original
                # deprecated route (no crash, no infinite loop).

            # Key-aware filter: if keyed is not None, only accept when the
            # resolved provider is in the keyed set OR it is the selected route.
            if keyed is None or resolved_provider in keyed:
                return ChildRoute(resolved_provider, resolved_model)
            # Not in keyed set - check if it is the selected (configured) route.
            if (
                sel_provider is not None
                and sel_model is not None
                and resolved_provider == sel_provider
                and resolved_model == sel_model
            ):
                return ChildRoute(resolved_provider, resolved_model)
            # Fall through to allowlist / selected check below.

    # Always accept if the (provider, model) matches the selected route
    # (handles custom model ids not in the static registry).
    if keyed is not None and sel_provider is not None and sel_model is not None:
        if provider.strip().casefold() == sel_provider and model.strip().casefold() == sel_model:
            return ChildRoute(provider, model)

    try:
        from magi_agent.config.env import (  # noqa: PLC0415
            operator_allowed_model_routes,
        )

        allowlist = operator_allowed_model_routes(env)
        if (provider.strip().casefold(), model.strip().casefold()) in allowlist:
            return ChildRoute(provider, model)
    except Exception:  # noqa: BLE001 — allowlist read must never block validation.
        pass
    return None


def available_child_model_routes(env: Mapping[str, str]) -> list[str]:
    """Sorted ``provider:model (tier)`` routes a child spawn may target.

    The union of the two sources :func:`resolve_child_route` accepts: the
    built-in :class:`ModelTierRegistry` AND the operator's deployment route
    allowlist. Single source of truth for both the SpawnAgent tool guidance and
    the system-prompt capability block, so the model is told exactly the routes
    that pass validation. A consistency test asserts every listed route resolves.

    When ``MAGI_KEY_AWARE_MODEL_ROUTES_ENABLED`` is ON and at least one provider
    key is configured, only routes whose provider is in the keyed set are included
    from the built-in registry (the operator allowlist is always included in full).
    The selected provider's own model is always included even if it is a custom id
    not present in the static registry.  When OFF or no keys are found, output is
    byte-identical to today (fail-open).  Fail-soft: any error contributes nothing.
    """
    # Compute keyed providers; None means "do not filter".
    keyed = _keyed_registry_providers(env)

    tiers: dict[str, str] = {}
    try:
        for (provider, model), record in ModelTierRegistry.with_defaults()._records.items():
            if keyed is not None and provider not in keyed:
                continue
            # PR-4: hide catalog-deprecated routes from the system-prompt feed
            # so the parent agent never picks a deprecated id from the visible
            # list. Fail-soft: any lookup error keeps the route in the output.
            deprecated, _ = _catalog_deprecation_lookup(provider, model)
            if deprecated:
                continue
            tiers[f"{provider}:{model}"] = str(getattr(record, "tier", "") or "")
    except Exception:  # noqa: BLE001 — registry read must never raise here.
        pass

    # When gate is ON, also ensure the selected provider's own model is present
    # (handles custom model ids not in the static registry).
    if keyed is not None:
        try:
            from magi_agent.engine.providers import resolve_provider_config  # noqa: PLC0415

            sel = resolve_provider_config(env=env)
            if sel:
                tiers.setdefault(f"{sel.provider}:{sel.model}", "")
        except Exception:  # noqa: BLE001 — fail-soft: selected provider is best-effort
            pass

    try:
        from magi_agent.config.env import (  # noqa: PLC0415
            operator_allowed_model_routes,
        )

        for provider, model in operator_allowed_model_routes(env):
            tiers.setdefault(f"{provider}:{model}", "")
    except Exception:  # noqa: BLE001 — allowlist read must never raise here.
        pass
    return [f"{route} ({tier})" if tier else route for route, tier in sorted(tiers.items())]


__all__ = [
    "ModelCapability",
    "ModelTier",
    "ModelTierPolicy",
    "ModelTierRegistry",
    "ModelUsagePhase",
    "ResolvedModelTier",
    "ChildRoute",
    "available_child_model_routes",
    "resolve_child_route",
]
