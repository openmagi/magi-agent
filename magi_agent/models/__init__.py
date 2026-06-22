"""Single source of truth for provider/model metadata (E-1).

The :class:`magi_agent.models.catalog.ModelCatalog` consolidates the formerly
five hand-maintained model lists (CLI default, registry, materializer cheap
table, dashboard presets, docs prose) into one packaged JSON record set.

Consumers:
- ``magi_agent.cli.providers`` reads per-provider defaults + litellm prefixes.
- ``magi_agent.runtime.model_tiers.ModelTierRegistry`` builds its ``with_defaults``
  records from the catalog (applying ``provider_aliases`` so a single canonical
  record fans out to both ``gemini`` and ``google`` registry labels).
- ``magi_agent.cli.real_runner._materializer_model`` queries the cheap tier.
- ``magi_agent.cli.app`` derives ``--model`` help text from the anthropic default.
- ``apps/web/src/lib/models/generated-local-runtime-models.ts`` is rendered by
  ``python -m magi_agent.models.export_ts`` and re-exported by the hand-written
  ``local-runtime-models.ts`` shim.

Adding a model: edit ``builtin_catalog.json`` and bump ``last_verified``.
Re-run ``python -m magi_agent.models.export_ts --out
apps/web/src/lib/models/generated-local-runtime-models.ts`` to refresh the TS
companion (a packaged test enforces freshness).
"""

from __future__ import annotations

from magi_agent.models.catalog import ModelCatalog, UnknownModelError
from magi_agent.models.types import ModelRecord

__all__ = ["ModelCatalog", "ModelRecord", "UnknownModelError"]
