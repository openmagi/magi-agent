"""CLI memory bootstrap: ``config.toml[memory]`` → process env (PR-C).

WHY THIS EXISTS
---------------
Every runtime memory gate (``memory_turn_hook``,
``adapters/hipocampus_readonly``, ``gates/*``, ``memory/policy``,
``adapters/local_file_writable``) calls
:func:`magi_agent.memory.config.resolve_memory_config` with NO args, so it reads
the **process ENV only** — the resolver's injectable ``config`` table is empty at
runtime.  That means a ``[memory]`` section in ``~/.magi/config.toml`` does
NOTHING at runtime by itself.

This module is the one-time CLI startup step that bridges the gap: it loads
``[memory]`` from the config file, overlays it on the **install defaults**, and
``setdefault``s the matching ``MAGI_MEMORY_*`` env vars so the existing
env-reading gates see them.

INSTALL-DEFAULT-ON, CODE-DEFAULT-OFF
------------------------------------
A fresh install should have memory ON.  We achieve that WITHOUT flipping the
code-level default in ``memory/config.py`` (so the repo's many memory-off tests
stay green): the bootstrap only runs from the real installed CLI entrypoints
(``cli.app:main`` / ``main:main``), never during library/pytest imports.  The
code default (``resolve_memory_config(env={}, config={})`` → master False) is
unchanged; tests never run this bootstrap and isolate via ``MAGI_CONFIG``.

OVERLAY + PRECEDENCE
--------------------
Effective memory settings = INSTALL DEFAULTS overlaid by ``config.toml[memory]``.

  * Install defaults: ``{enabled: True, prefer_local_search: True}`` (only these
    two ON; the master cascade in :func:`resolve_memory_config` derives
    write/recall/projection/compaction from ``enabled``, and the remaining
    opt-ins stay OFF).
  * ``config.toml[memory]`` keys (snake_case, mirroring the env names) override
    the install defaults: e.g. ``[memory] enabled = false`` → memory OFF;
    ``[memory] prefer_local_search = false`` → that opt-in OFF.
  * Each resulting bool is applied via ``os.environ.setdefault`` so an explicit
    pre-set env var STILL WINS.  Precedence: ``env > config > install-default``.

Result: installed CLI → memory on (master ⇒ write/flush/compaction/projection +
``prefer_local_search`` ⇒ per-turn ``<memory-recall>``).  qmd stays opt-in
(PyBM25 is the zero-dep default; ``prefer_qmd_auto_register`` is NOT enabled).

FAIL-SOFT
---------
A malformed / unreadable ``config.toml`` must never crash startup — the loader
(:func:`magi_agent.cli.providers._load_config_file`) already returns ``{}`` on
error, so we fall back to the install defaults.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping, MutableMapping

from magi_agent.harness.memory_session_extract import (
    MAGI_MEMORY_SESSION_EXTRACT_ENABLED_ENV,
)
from magi_agent.memory.config import (
    MASTER_ENV_VAR,
    PREFER_LOCAL_SEARCH_ENV_VAR,
    coerce_bool,
)
from magi_agent.memory.config import _memory_table as _config_memory_table

logger = logging.getLogger(__name__)

#: Install defaults: the memory settings a fresh install turns ON.  Mapped to
#: config keys (snake_case, mirroring ``resolve_memory_config``'s ``config_key``)
#: and the matching ``MAGI_MEMORY_*`` env var (NEVER hardcode the env-var string
#: here — import the registry constant).
#:
#: ``enabled`` (master) ON ⇒ write/recall/projection/compaction cascade ON.
#: ``prefer_local_search`` ON ⇒ per-turn ``<memory-recall>`` recall path.
#: ``session_extract_enabled`` ON ⇒ session-end fact extraction (a strict opt-in
#:   that does NOT cascade from master, so it is listed explicitly). The write it
#:   performs still requires the (cascaded-ON) write gate, and the extractor
#:   degrades to a no-op when no provider/key is configured.
#: Everything else absent ⇒ resolver's master cascade / opt-in defaults apply.
_INSTALL_DEFAULT_KEYS: tuple[tuple[str, str, bool], ...] = (
    # (config_key, env_var, install_default_value)
    ("enabled", MASTER_ENV_VAR, True),
    ("prefer_local_search", PREFER_LOCAL_SEARCH_ENV_VAR, True),
    ("session_extract_enabled", MAGI_MEMORY_SESSION_EXTRACT_ENABLED_ENV, True),
)


def apply_memory_config_bootstrap(
    environ: MutableMapping[str, str],
    *,
    config: Mapping[str, object] | None = None,
) -> None:
    """Overlay ``config.toml[memory]`` on install defaults into ``environ``.

    Runs ONCE at real CLI startup (``cli.app:main`` / ``main:main``).  For each
    install-default memory setting, computes ``config.toml[memory]`` override (if
    present) else the install default, then ``setdefault``s the matching env var
    to ``"1"``/``"0"`` — so an explicit pre-set env var still wins.

    Fail-soft: any error (malformed config, unexpected type) is logged and
    swallowed; the install defaults are applied where they were reached and the
    CLI continues to start.

    Args:
        environ: The process env to mutate (normally ``os.environ``).
        config: Parsed config dict; loaded from ``~/.magi/config.toml`` (via
            ``providers._load_config_file``) when omitted.  Injectable for tests.
    """
    try:
        table = _memory_table(config)
    except Exception:  # pragma: no cover - defensive; loader is already fail-soft
        logger.debug("memory bootstrap: config load failed; using install defaults", exc_info=True)
        table = {}

    for config_key, env_var, install_default in _INSTALL_DEFAULT_KEYS:
        try:
            effective = _effective_bool(table, config_key, install_default)
            environ.setdefault(env_var, "1" if effective else "0")
        except Exception:  # pragma: no cover - defensive; keep startup alive
            logger.debug(
                "memory bootstrap: failed to apply %s; skipping", env_var, exc_info=True
            )


def _memory_table(config: Mapping[str, object] | None) -> Mapping[str, object]:
    """Return the ``[memory]`` table from ``config`` (or ``~/.magi/config.toml``).

    The dict → ``[memory]`` extraction reuses
    :func:`magi_agent.memory.config._memory_table` so the two modules share one
    table-extraction rule; the bootstrap adds only the file-load when ``config``
    is omitted.
    """
    if config is None:
        from magi_agent.cli.providers import _load_config_file  # noqa: PLC0415

        config = _load_config_file()
    return _config_memory_table(config)


def _effective_bool(
    table: Mapping[str, object], config_key: str, install_default: bool
) -> bool:
    """config.toml value (coerced) if present+valid, else the install default."""
    if config_key in table:
        coerced = coerce_bool(table.get(config_key))
        if coerced is not None:
            return coerced
    return install_default


__all__ = ["apply_memory_config_bootstrap"]
