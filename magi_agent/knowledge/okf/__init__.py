"""OKF (Open Knowledge Format) knowledge-bundle adapter — PR1 (pure, unwired).

A trust-path read-only knowledge source for OKF bundles (nested folders +
markdown + YAML frontmatter).  PR1 ships a pure, default-OFF loader + config +
lexical matcher with no runtime wiring (no tool registration, catalog, boundary,
or prompt-assembly changes).  Deliberately decoupled from ``magi_agent.memory``
and from ``magi_agent.knowledge.provider_boundary`` (the design rejects routing
trusted content through that fake-provider redaction boundary).
"""
from __future__ import annotations

from magi_agent.knowledge.okf.bundle_loader import (
    OkfBundleIndex,
    OkfDoc,
    load_bundles,
)
from magi_agent.knowledge.okf.config import (
    MAX_DOC_BYTES,
    OkfConfig,
    resolve_okf_config,
)
from magi_agent.knowledge.okf.matcher import match_score

__all__ = [
    "MAX_DOC_BYTES",
    "OkfBundleIndex",
    "OkfConfig",
    "OkfDoc",
    "load_bundles",
    "match_score",
    "resolve_okf_config",
]
