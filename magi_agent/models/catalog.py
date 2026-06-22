"""Single source of truth: loads ``builtin_catalog.json`` once and serves it.

Consumers (``cli/providers``, ``runtime/model_tiers``, ``cli/real_runner``,
``cli/app``, and the TS exporter) MUST go through :class:`ModelCatalog` so the
five formerly-hand-maintained model tables stay in sync. The catalog ships in
the wheel via ``[tool.setuptools.package-data]`` and is loaded through
``importlib.resources`` so a pip-installed runtime resolves it identically to
an editable install.
"""

from __future__ import annotations

import importlib.resources as resources
import json
from functools import lru_cache
from typing import Mapping

from magi_agent.models.types import ModelRecord


class UnknownModelError(KeyError):
    """Raised when a provider/model the catalog does not know is requested.

    Subclasses :class:`KeyError` so existing ``except KeyError`` paths (e.g.
    legacy ``_DEFAULT_MODEL[provider]`` indexing) still catch it during the
    refactor; consumers that want loud failure (a stale built-in default)
    should catch :class:`UnknownModelError` specifically.
    """


_CATALOG_RESOURCE = "builtin_catalog.json"


def _load_payload() -> dict[str, object]:
    """Load ``builtin_catalog.json`` from the installed-layout path."""
    files = resources.files("magi_agent.models")
    text = (files / _CATALOG_RESOURCE).read_text(encoding="utf-8")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise UnknownModelError(
            f"{_CATALOG_RESOURCE} root must be a JSON object; got {type(payload).__name__}"
        )
    return payload


class ModelCatalog:
    """Frozen catalog of ``ModelRecord``s plus a ``provider_aliases`` map.

    Built once via :meth:`builtin` (lru-cached) and consumed read-only by every
    downstream module. The class is intentionally NOT a pydantic model so the
    cached singleton can hold the parsed ``ModelRecord`` tuples without
    re-validation on every access.
    """

    __slots__ = ("_records", "_by_key", "_aliases", "_schema_version")

    def __init__(
        self,
        records: tuple[ModelRecord, ...],
        provider_aliases: Mapping[str, str],
        schema_version: int = 1,
    ) -> None:
        self._records = records
        self._by_key: dict[tuple[str, str], ModelRecord] = {
            (r.provider, r.model): r for r in records
        }
        # Frozen alias map (callers receive a copy via :meth:`provider_aliases`).
        self._aliases: dict[str, str] = dict(provider_aliases)
        self._schema_version = schema_version

    # ------------------------------------------------------------------ load

    @classmethod
    def builtin(cls) -> "ModelCatalog":
        """Cached singleton loaded from ``builtin_catalog.json``."""
        return _cached_builtin()

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "ModelCatalog":
        """Build a catalog from a parsed JSON payload (testing seam)."""
        raw_records = payload.get("records") or []
        if not isinstance(raw_records, list):
            raise UnknownModelError(
                "catalog payload 'records' must be a list"
            )
        records = tuple(
            ModelRecord.model_validate(item) for item in raw_records
        )
        raw_aliases = payload.get("provider_aliases") or {}
        if not isinstance(raw_aliases, dict):
            raise UnknownModelError(
                "catalog payload 'provider_aliases' must be a mapping"
            )
        aliases: dict[str, str] = {
            str(k): str(v) for k, v in raw_aliases.items()
        }
        schema_version = int(payload.get("schema_version", 1) or 1)
        return cls(
            records=records,
            provider_aliases=aliases,
            schema_version=schema_version,
        )

    # ----------------------------------------------------------------- query

    def provider_aliases(self) -> dict[str, str]:
        """Copy of the canonical provider-aliases map (``alias -> canonical``).

        Currently ``{"google": "gemini"}`` so the registry can fan a single
        gemini record out under both labels without storing duplicates.
        """
        return dict(self._aliases)

    def _canonical_provider(self, provider: str) -> str:
        """Resolve ``google`` → ``gemini`` (or any other catalog alias)."""
        return self._aliases.get(provider, provider)

    def all_records(self) -> tuple[ModelRecord, ...]:
        return self._records

    def record(self, provider: str, model: str) -> ModelRecord | None:
        """Look up a record by canonical ``(provider, model)``.

        Returns ``None`` (not raise) for unknown ids — callers that want loud
        failure use :meth:`default_model_for`/:meth:`cheap_model_for`.
        """
        canonical = self._canonical_provider(provider)
        return self._by_key.get((canonical, model))

    def default_model_for(self, provider: str) -> ModelRecord:
        """Per-provider default record (first non-deprecated entry in JSON order).

        JSON authoring order is the single tiebreaker — put the desired default
        FIRST under each provider block in ``builtin_catalog.json``. This keeps
        the legacy ``_DEFAULT_MODEL`` mapping (anthropic→sonnet-4-6, gemini→
        flash, fireworks→kimi-k2p6) byte-identical without a separate
        ``is_default: true`` flag. Raises :class:`UnknownModelError` for an
        unknown provider OR for a provider with no non-deprecated record (the
        latter is a catalog authoring bug).
        """
        canonical = self._canonical_provider(provider)
        for r in self._records:
            if (
                r.provider == canonical
                and not r.deprecated
                and r.litellm_prefix
            ):
                return r
        raise UnknownModelError(
            f"no catalogued model for provider {provider!r}; "
            "edit magi_agent/models/builtin_catalog.json"
        )

    def cheap_model_for(self, provider: str) -> ModelRecord:
        """Cheap-tier record for ``provider`` (materializer / scratchpad path).

        First non-deprecated ``tier="cheap"`` record under ``provider`` (JSON
        order tiebreaker). Falls back to :meth:`default_model_for` when no
        cheap record exists. Raises :class:`UnknownModelError` for an unknown
        provider.
        """
        canonical = self._canonical_provider(provider)
        for r in self._records:
            if (
                r.provider == canonical
                and r.tier == "cheap"
                and not r.deprecated
                and r.litellm_prefix
            ):
                return r
        return self.default_model_for(provider)

    def context_window(self, model: str) -> int | None:
        """First catalogued ``context_window`` whose record.model matches.

        Spans providers (matches the legacy ``_KNOWN_TOKEN_LIMITS`` semantics
        in ``runtime/message_builder``/``context/token_tracker``).
        """
        for r in self._records:
            if r.model == model:
                return r.context_window
        return None

    def is_router_alias(self, model: str) -> bool:
        """``True`` when ``model`` matches any ``source="router"`` record."""
        for r in self._records:
            if r.model == model and r.source == "router":
                return True
        return False


@lru_cache(maxsize=1)
def _cached_builtin() -> ModelCatalog:
    return ModelCatalog.from_payload(_load_payload())


__all__ = ["ModelCatalog", "UnknownModelError"]
