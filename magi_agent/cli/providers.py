"""Provider/key resolution for the local ``magi`` CLI.

The installed CLI ships a model-free stub runner (:mod:`magi_agent.cli.local_runner`)
so ``magi`` launches without any configuration. This module is the bridge to a
*real* runner: it discovers which model provider the user has configured and the
API key to use, from either a config file (``~/.magi/config.toml``, overridable
via ``MAGI_CONFIG``) or environment variables.

Five providers are supported, all routed through ADK's ``LiteLlm``:
``openai``, ``anthropic``, ``gemini``, ``fireworks`` and ``openrouter`` (a
meta-router whose model id is itself a ``<vendor>/<model>`` slug).

Resolution rules
----------------
1. If a provider is explicitly named (``[model].provider`` in the config file or
   ``MAGI_PROVIDER`` in the env), use it. Its key comes from ``[model].api_key``,
   then ``[providers.<name>].api_key``, then the provider's env var(s).
2. Otherwise auto-detect: walk :data:`SUPPORTED_PROVIDERS` in order and use the
   first provider that has a key available (config or env).
3. If nothing is configured, return ``None`` so the caller keeps the stub runner.
"""

from __future__ import annotations

import math
import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

# ---------------------------------------------------------------------------
# Minimal TOML serializer (tomli_w is not in the project's dependencies; we
# keep this small and handle the value types that appear in real config.toml:
# str, bool, int, finite-float, list (of the above), and nested tables (dicts).
#
# Anything not faithfully renderable — datetimes, None, sets, inf/nan, nested
# dicts inside arrays (array-of-tables) — raises ValueError immediately rather
# than emitting a corrupt/lossy representation.
# ---------------------------------------------------------------------------

# Control-character escape map for TOML basic strings.
_TOML_ESCAPES: dict[str, str] = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _escape_toml_string(s: str) -> str:
    """Return the content of a TOML basic string (without surrounding quotes)."""
    parts: list[str] = []
    for ch in s:
        if ch in _TOML_ESCAPES:
            parts.append(_TOML_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            # Other control chars: use \uXXXX escape.
            parts.append(f"\\u{ord(ch):04X}")
        else:
            parts.append(ch)
    return "".join(parts)


def _toml_value(value: object) -> str:
    """Render a scalar, list, or nested-table value as a TOML literal.

    Raises ``ValueError`` for any type/value that cannot be faithfully
    represented in TOML by this serializer (e.g. datetimes, None, sets,
    inf, nan, arrays-of-tables).
    """
    if isinstance(value, bool):  # bool BEFORE int (bool is a subclass of int)
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError(
                f"Cannot serialize float {value!r} to TOML: "
                "inf and nan are not valid TOML float literals."
            )
        return repr(value)
    if isinstance(value, str):
        return f'"{_escape_toml_string(value)}"'
    if isinstance(value, (list, tuple)):
        # Render as a TOML inline array.  Each element must be a scalar
        # (dict elements would require array-of-tables syntax which we don't
        # support here — raise so the round-trip self-check catches it).
        rendered_elems: list[str] = []
        for elem in value:
            if isinstance(elem, dict):
                raise ValueError(
                    "Cannot serialize a list containing dicts (array-of-tables) "
                    "with this minimal TOML writer."
                )
            rendered_elems.append(_toml_value(elem))
        return "[" + ", ".join(rendered_elems) + "]"
    # Unsupported type (datetime, None, set, …) — raise instead of silently dropping.
    raise ValueError(
        f"Cannot serialize value of type {type(value).__name__!r} to TOML: {value!r}. "
        "Aborting to prevent config data loss."
    )


def _render_toml(data: dict[str, object]) -> str:
    """Serialize ``data`` to a minimal TOML string (sections + key=value).

    Handles arbitrarily nested tables by emitting ``[a.b.c]`` section headers
    for nested dict values.  Scalars and arrays at each level are emitted as
    ``key = val`` lines immediately after the section header.

    Raises ``ValueError`` for any value type that cannot be faithfully rendered.
    """
    lines: list[str] = []

    def _emit_section(d: dict[str, object], prefix: str) -> None:
        """Emit the non-dict keys of ``d`` then recurse into sub-tables."""
        # Non-table values first (scalars + inline arrays).
        for key, value in d.items():
            if not isinstance(value, dict):
                rendered = _toml_value(value)
                lines.append(f"{key} = {rendered}")
        # Sub-tables after (each gets its own [prefix.key] header).
        for key, value in d.items():
            if isinstance(value, dict):
                section_name = f"{prefix}.{key}" if prefix else key
                lines.append(f"\n[{section_name}]")
                _emit_section(value, section_name)

    # Top-level non-table values (no section header needed).
    for key, value in data.items():
        if not isinstance(value, dict):
            rendered = _toml_value(value)
            lines.append(f"{key} = {rendered}")
    # Top-level tables.
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"\n[{key}]")
            _emit_section(value, key)

    result = "\n".join(lines)
    if result and not result.endswith("\n"):
        result += "\n"
    return result

from magi_agent.config.env import LOCAL_DEV_MODEL_SENTINEL

# Auto-detect order. Anthropic first (magi's primary deployment posture), then
# the rest. Also the set of accepted ``provider`` values. OpenRouter is last: it
# is a meta-router (one key fronting many upstream models), so a direct provider
# key wins auto-detect and OpenRouter is opted into explicitly (``MAGI_PROVIDER=
# openrouter`` / ``[model].provider``).
SUPPORTED_PROVIDERS: tuple[str, ...] = (
    "anthropic",
    "openai",
    "gemini",
    "fireworks",
    "openrouter",
)

# Env var(s) carrying the API key for each provider, in lookup order.
_PROVIDER_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "fireworks": ("FIREWORKS_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
}

# Default model id and litellm prefix per provider, sourced from the single
# ``ModelCatalog`` (E-1). Edit ``magi_agent/models/builtin_catalog.json`` (and
# regenerate the TS companion via ``python -m magi_agent.models.export_ts``) to
# change either value. Computed once at import: the catalog is itself a cached
# singleton, so dict comprehensions here are fixed-cost startup work.
def _provider_default_table() -> dict[str, str]:
    from magi_agent.models.catalog import ModelCatalog  # noqa: PLC0415

    catalog = ModelCatalog.builtin()
    return {p: catalog.default_model_for(p).model for p in SUPPORTED_PROVIDERS}


def _provider_litellm_prefix_table() -> dict[str, str]:
    from magi_agent.models.catalog import ModelCatalog  # noqa: PLC0415

    catalog = ModelCatalog.builtin()
    return {p: catalog.default_model_for(p).litellm_prefix for p in SUPPORTED_PROVIDERS}


_DEFAULT_MODEL: dict[str, str] = _provider_default_table()
_LITELLM_PREFIX: dict[str, str] = _provider_litellm_prefix_table()


class UnknownProviderError(ValueError):
    """Raised when an explicitly-configured provider is not supported."""


@dataclass(frozen=True)
class ProviderConfig:
    """A fully-resolved provider selection ready to build a model from."""

    provider: str
    model: str
    api_key: str

    @property
    def litellm_model(self) -> str:
        return f"{_LITELLM_PREFIX[self.provider]}/{self.model}"


def _config_path() -> Path:
    override = os.environ.get("MAGI_CONFIG")
    if override and override.strip():
        return Path(override).expanduser()
    return Path.home() / ".magi" / "config.toml"


def _load_config_file() -> dict[str, object]:
    path = _config_path()
    try:
        with open(path, "rb") as handle:
            loaded = tomllib.load(handle)
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return {}
    except (OSError, tomllib.TOMLDecodeError):
        # A malformed or unreadable config must not crash the CLI; fall back to
        # env-only resolution (which may itself yield None -> stub runner).
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _clean(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _clean_model(value: object) -> str | None:
    cleaned = _clean(value)
    if cleaned is None or cleaned == LOCAL_DEV_MODEL_SENTINEL:
        return None
    return cleaned


def _section(config: Mapping[str, object], name: str) -> dict[str, object]:
    section = config.get(name)
    return section if isinstance(section, dict) else {}


def _resolve_provider_key(
    provider: str,
    *,
    env: Mapping[str, str],
    providers_section: Mapping[str, object],
) -> str | None:
    """Return the API key for *provider* using the canonical precedence:
    ``[providers.<name>].api_key`` in config first, then each env var in
    ``_PROVIDER_ENV_KEYS[provider]``.  Returns ``None`` if no usable key is found.

    This is the single source of truth shared by :func:`resolve_provider_config`
    and :func:`configured_providers` so precedence cannot drift.
    """
    provider_block = providers_section.get(provider)
    if isinstance(provider_block, dict):
        configured = _clean(provider_block.get("api_key"))
        if configured:
            return configured
    for env_name in _PROVIDER_ENV_KEYS[provider]:
        from_env = _clean(env.get(env_name))
        if from_env:
            return from_env
    return None


def configured_providers(
    *,
    env: Mapping[str, str] | None = None,
    config: Mapping[str, object] | None = None,
) -> list[str]:
    """All SUPPORTED_PROVIDERS that have a resolvable key (config or env),
    in SUPPORTED_PROVIDERS order."""
    env = os.environ if env is None else env
    config = _load_config_file() if config is None else config

    providers_section = _section(config, "providers")
    return [
        provider
        for provider in SUPPORTED_PROVIDERS
        if _resolve_provider_key(provider, env=env, providers_section=providers_section)
    ]


def default_model_for(provider: str) -> str:
    """Return the best-effort default model id for ``provider``.

    Mirrors :func:`resolve_provider_config`'s handling of unsupported providers:
    an unknown provider raises :class:`UnknownProviderError` rather than guessing
    a fallback. Used by TUI dialogs to list candidate models without a key.
    """

    try:
        return _DEFAULT_MODEL[provider]
    except KeyError:
        raise UnknownProviderError(
            f"Unsupported provider {provider!r}. "
            f"Supported: {', '.join(SUPPORTED_PROVIDERS)}."
        ) from None


def _split_provider_slug(value: str | None) -> tuple[str | None, str | None]:
    """If ``value`` is a ``<provider>/<model>`` slug, return ``(provider, model)``.

    Recognizes only the known provider prefixes (``SUPPORTED_PROVIDERS`` plus the
    ``google`` alias for ``gemini``) so the Fireworks raw model id
    ``accounts/fireworks/models/...`` — which also contains slashes — is
    correctly treated as a bare id, not as a provider/<model> slug. Returns
    ``(None, None)`` for an unprefixed id, an empty string, or ``None``.
    """
    cleaned = _clean(value)
    if cleaned is None or "/" not in cleaned:
        return None, None
    prefix, _, rest = cleaned.partition("/")
    prefix_lc = prefix.lower()
    canonical = "gemini" if prefix_lc == "google" else prefix_lc
    if canonical not in SUPPORTED_PROVIDERS or not rest.strip():
        return None, None
    return canonical, rest.strip()


def resolve_provider_config(
    *,
    model_override: str | None = None,
    env: Mapping[str, str] | None = None,
    config: Mapping[str, object] | None = None,
) -> ProviderConfig | None:
    """Resolve the active provider config, or ``None`` if none is configured.

    ``env`` and ``config`` are injectable for testing; they default to the real
    process environment and ``~/.magi/config.toml``.

    A ``model_override`` that carries a ``<provider>/<model>`` slug (e.g. the
    chat picker sending ``anthropic/claude-sonnet-4-6``) ALSO switches the
    provider — otherwise the config's default provider would be combined with
    the override's model id, which LiteLLM then proxies through the wrong
    provider API ("openai does not support parameters: ['reasoning_effort'],
    for model=anthropic/claude-sonnet-4-6").
    """

    env = os.environ if env is None else env
    config = _load_config_file() if config is None else config

    model_section = _section(config, "model")
    providers_section = _section(config, "providers")

    def key_for(provider: str) -> str | None:
        return _resolve_provider_key(provider, env=env, providers_section=providers_section)

    # If the override carries a `<provider>/<model>` slug, that provider wins
    # over the config's default — the user just picked a model on that
    # provider, so we must call THAT provider, not the config one.
    slug_provider, slug_model = _split_provider_slug(model_override)
    effective_override_model = slug_model if slug_model is not None else model_override

    def model_for(provider: str) -> str:
        return (
            _clean_model(effective_override_model)
            or _clean_model(env.get("MAGI_MODEL"))
            or _clean_model(model_section.get("model"))
            or _DEFAULT_MODEL[provider]
        )

    if slug_provider is not None:
        api_key = key_for(slug_provider)
        if not api_key:
            # No key for the slug-named provider → fall back to the stub
            # runner instead of calling a different provider with the wrong key.
            return None
        return ProviderConfig(
            provider=slug_provider, model=model_for(slug_provider), api_key=api_key
        )

    explicit = _clean(env.get("MAGI_PROVIDER")) or _clean(model_section.get("provider"))
    if explicit:
        provider = explicit.lower()
        if provider not in SUPPORTED_PROVIDERS:
            raise UnknownProviderError(
                f"Unsupported provider {provider!r}. "
                f"Supported: {', '.join(SUPPORTED_PROVIDERS)}."
            )
        api_key = _clean(model_section.get("api_key")) or key_for(provider)
        if not api_key:
            # Provider named but no key found -> fall back to the stub runner
            # rather than crashing, so ``magi`` still launches.
            return None
        return ProviderConfig(provider=provider, model=model_for(provider), api_key=api_key)

    for provider in SUPPORTED_PROVIDERS:
        api_key = key_for(provider)
        if api_key:
            return ProviderConfig(provider=provider, model=model_for(provider), api_key=api_key)
    return None


def resolve_vision_provider_config(
    *,
    env: Mapping[str, str] | None = None,
    config: Mapping[str, object] | None = None,
) -> ProviderConfig | None:
    """Resolve the vision-sidecar override, or ``None`` for the default path.

    The override is triggered by ``MAGI_VISION_MODEL`` (bare provider-native
    model id, same semantics as ``MAGI_MODEL``). ``MAGI_VISION_PROVIDER``
    optionally names which provider's credentials to use; when unset, the main
    resolved provider's credentials are reused with only the model swapped.

    Returns ``None`` when ``MAGI_VISION_MODEL`` is unset/blank/sentinel, when
    an explicit ``MAGI_VISION_PROVIDER`` is unsupported or has no resolvable
    API key, or when no main provider exists to inherit credentials from.
    NEVER raises (this feeds the fail-soft tool path in ``image_tools``).
    """

    try:
        env = os.environ if env is None else env

        model = _clean_model(env.get("MAGI_VISION_MODEL"))
        if model is None:
            return None

        config = _load_config_file() if config is None else config

        vision_provider = _clean(env.get("MAGI_VISION_PROVIDER"))
        if vision_provider is not None:
            provider = vision_provider.lower()
            if provider not in SUPPORTED_PROVIDERS:
                # Unlike resolve_provider_config (UnknownProviderError), a tool-path
                # misconfiguration must degrade to the main path, not crash.
                return None
            api_key: str | None = None
            provider_block = _section(config, "providers").get(provider)
            if isinstance(provider_block, dict):
                api_key = _clean(provider_block.get("api_key"))
            if not api_key:
                for env_name in _PROVIDER_ENV_KEYS[provider]:
                    from_env = _clean(env.get(env_name))
                    if from_env:
                        api_key = from_env
                        break
            if not api_key:
                return None
            return ProviderConfig(provider=provider, model=model, api_key=api_key)

        # No explicit vision provider: inherit the main provider's credentials.
        main = resolve_provider_config(env=env, config=config)
        if main is None:
            return None
        return ProviderConfig(provider=main.provider, model=model, api_key=main.api_key)
    except Exception:  # noqa: BLE001 — tool path: degrade to the main path, never crash.
        return None


def model_choices_from_config(current: str | None = None) -> list[str]:
    """Return candidate model ids with ``current`` first (if known).

    Delegates to :func:`magi_agent.cli.tui.dialogs.model.model_choices` so the
    list is always in sync with the picker dialog.  This thin wrapper lives in
    ``providers.py`` so ``control.py`` can import it without pulling in
    ``textual`` at the top level (the dialog module is imported lazily inside
    the wrapper function call).
    """

    from magi_agent.cli.tui.dialogs.model import model_choices  # noqa: PLC0415

    return model_choices(current)


def persist_provider_keys(
    updates: Mapping[str, str | None],
    *,
    active: str | None = None,
    models: Mapping[str, str | None] | None = None,
    path: Path | None = None,
) -> None:
    """Write per-provider API keys (and optionally per-provider models) to the config file.

    - ``updates`` maps provider name to key value.  A non-empty cleaned value
      sets ``[providers.<name>].api_key = value``; an empty string or ``None``
      deletes that key (and drops the empty ``[providers.<name>]`` table).
    - ``models`` maps provider name to model string.  A non-empty value sets
      ``[providers.<name>].model = value``; ``None`` or empty string deletes it.
      Both keys and models are written in the SAME single atomic write so the
      file is always left at ``0600``.
    - Provider names are validated against ``SUPPORTED_PROVIDERS``; ``"google"``
      is canonicalized to ``"gemini"`` (mirror :func:`_canonical_provider`).
      Unknown names raise :class:`UnknownProviderError`.
    - If ``active`` is given and valid, also writes ``[model].provider = active``.
    - NEVER clobbers unrelated sections/keys.  Round-trip self-check before
      writing.  ``path`` overrides the default config path for tests.
    - File is written with ``0600`` permissions (owner read/write only).
    """
    # --- canonicalize and validate provider names ---
    canonical_updates: dict[str, str | None] = {}
    for raw_name, key_value in updates.items():
        normalized = raw_name.strip().lower() if isinstance(raw_name, str) else ""
        if normalized == "google":
            normalized = "gemini"
        if normalized not in SUPPORTED_PROVIDERS:
            raise UnknownProviderError(
                f"Unsupported provider {raw_name!r}. "
                f"Supported: {', '.join(SUPPORTED_PROVIDERS)}."
            )
        canonical_updates[normalized] = key_value

    canonical_active: str | None = None
    if active is not None:
        norm_active = active.strip().lower() if isinstance(active, str) else ""
        if norm_active == "google":
            norm_active = "gemini"
        if norm_active not in SUPPORTED_PROVIDERS:
            raise UnknownProviderError(
                f"Unsupported provider {active!r}. "
                f"Supported: {', '.join(SUPPORTED_PROVIDERS)}."
            )
        canonical_active = norm_active

    config_path = path if path is not None else _config_path()

    # Load existing config (tolerate missing/garbled → {}).
    try:
        with open(config_path, "rb") as fh:
            raw: dict[str, object] = tomllib.load(fh)
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        raw = {}
    except (OSError, tomllib.TOMLDecodeError):
        raw = {}

    raw = dict(raw)  # shallow copy top-level

    # Mutate [providers.<name>] for each update.
    providers_top = raw.get("providers")
    providers_section: dict[str, object] = (
        dict(providers_top) if isinstance(providers_top, dict) else {}
    )

    for provider, key_value in canonical_updates.items():
        cleaned = _clean(key_value)
        if cleaned:
            # Set the api_key in this provider's sub-table.
            existing_block = providers_section.get(provider)
            block: dict[str, object] = (
                dict(existing_block) if isinstance(existing_block, dict) else {}
            )
            block["api_key"] = cleaned
            providers_section[provider] = block
        else:
            # Empty/None → remove the key and drop the empty table.
            existing_block = providers_section.get(provider)
            if isinstance(existing_block, dict):
                block = dict(existing_block)
                block.pop("api_key", None)
                if block:
                    providers_section[provider] = block
                else:
                    providers_section.pop(provider, None)
            else:
                providers_section.pop(provider, None)

    # Apply per-provider model overrides in the SAME write (no chmod race).
    if models:
        for raw_pname, pmodel in models.items():
            normalized_pname = raw_pname.strip().lower() if isinstance(raw_pname, str) else ""
            if normalized_pname == "google":
                normalized_pname = "gemini"
            if normalized_pname not in SUPPORTED_PROVIDERS:
                raise UnknownProviderError(
                    f"Unsupported provider {raw_pname!r}. "
                    f"Supported: {', '.join(SUPPORTED_PROVIDERS)}."
                )
            cleaned_model = _clean(pmodel)
            existing_block = providers_section.get(normalized_pname)
            m_block: dict[str, object] = (
                dict(existing_block) if isinstance(existing_block, dict) else {}
            )
            if cleaned_model:
                m_block["model"] = cleaned_model
            else:
                m_block.pop("model", None)
            if m_block:
                providers_section[normalized_pname] = m_block
            else:
                providers_section.pop(normalized_pname, None)

    if providers_section:
        raw["providers"] = providers_section
    else:
        raw.pop("providers", None)

    # Optionally set [model].provider.
    if canonical_active is not None:
        model_section = raw.get("model")
        model_dict: dict[str, object] = (
            dict(model_section) if isinstance(model_section, dict) else {}
        )
        model_dict["provider"] = canonical_active
        raw["model"] = model_dict

    # Round-trip self-check: render → re-parse and assert equality BEFORE writing.
    rendered = _render_toml(raw)  # may raise ValueError for unrenderable types
    reparsed = tomllib.loads(rendered)
    if reparsed != raw:
        raise ValueError(
            "TOML round-trip self-check failed: the rendered config does not "
            "re-parse to the intended dict. Aborting to preserve the original file."
        )

    # Atomic write: create temp file with 0600 from the start (M3 defense-in-depth:
    # secret is never briefly world-readable at the default umask), then replace.
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config_path.with_suffix(".toml.tmp")
    try:
        fd = os.open(
            tmp_path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            stat.S_IRUSR | stat.S_IWUSR,  # 0o600
        )
        try:
            os.write(fd, rendered.encode("utf-8"))
        finally:
            os.close(fd)
        tmp_path.replace(config_path)
    finally:
        # Clean up temp if replace failed.
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _infer_provider_for_model(model_id: str) -> str | None:
    """Best-effort: map a bare model id to its provider, else None.

    Anchored on stable id families (``claude-*`` → anthropic, ``gpt-*``/``o*-``
    → openai, ``gemini-*`` → gemini, ``accounts/fireworks/*`` / ``kimi-*`` /
    ``minimax-*`` → fireworks, ``<vendor>/<model>`` slug → openrouter). Returns
    ``None`` for a model whose provider cannot be unambiguously determined so the
    caller keeps the existing provider rather than guessing wrong.
    """
    text = model_id.strip().lower()
    if not text:
        return None
    if text.startswith("accounts/fireworks/") or text.startswith("kimi-") or text.startswith("minimax-"):
        return "fireworks"
    if text.startswith("claude-"):
        return "anthropic"
    if text.startswith("gemini-"):
        return "gemini"
    if text.startswith("gpt-") or text.startswith("o1-") or text.startswith("o3-") or text.startswith("o4-"):
        return "openai"
    # ``<vendor>/<model>`` slug is OpenRouter's id shape (e.g. "openai/gpt-5.5").
    if "/" in text and not text.startswith("accounts/"):
        return "openrouter"
    return None


def persist_model(model_id: str, *, path: Path | None = None) -> None:
    """Write ``model_id`` to ``[model].model`` in the magi config file.

    - Reads the existing config (tolerates missing file — starts from empty).
    - Sets ``[model].model = <model_id>`` WITHOUT clobbering other sections/keys.
    - Writes back atomically (temp file + replace) and creates ``~/.magi/`` if
      needed.
    - ``path`` overrides the default config path for tests so tests NEVER touch
      the real ``~/.magi/config.toml``.
    """
    config_path = path if path is not None else _config_path()
    # Load existing config so we don't lose other sections/keys.
    try:
        with open(config_path, "rb") as fh:
            raw: dict[str, object] = tomllib.load(fh)
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        raw = {}
    except (OSError, tomllib.TOMLDecodeError):
        raw = {}

    # Ensure [model] section exists and set .model within it.
    model_section = raw.get("model")
    if not isinstance(model_section, dict):
        model_section = {}
    model_section = dict(model_section)  # shallow copy — don't mutate original
    model_section["model"] = model_id
    # Provider/model coherence: a bare model id whose provider is unambiguously
    # inferable overrides any stale provider, so the file never holds an
    # impossible pair like ``provider=fireworks, model=gpt-5.5``. When inference
    # is ambiguous (custom id, fine-tune), keep the existing provider — and when
    # neither is present nothing is written so the file is never half-set.
    inferred_provider = _infer_provider_for_model(model_id)
    if inferred_provider is not None:
        model_section["provider"] = inferred_provider
    raw = dict(raw)  # shallow copy top level
    raw["model"] = model_section

    # Round-trip self-check: render → re-parse and assert equality BEFORE writing.
    # If _render_toml raises (unrenderable value) or the round-trip check fails,
    # we abort here — BEFORE touching the file — so the original is never lost.
    rendered = _render_toml(raw)  # may raise ValueError for unrenderable types
    reparsed = tomllib.loads(rendered)
    if reparsed != raw:
        raise ValueError(
            "TOML round-trip self-check failed: the rendered config does not "
            "re-parse to the intended dict. Aborting to preserve the original file."
        )

    # Atomic write: temp file in same dir then os.replace.
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config_path.with_suffix(".toml.tmp")
    try:
        tmp_path.write_text(rendered, encoding="utf-8")
        tmp_path.replace(config_path)
    finally:
        # Clean up temp if replace failed.
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


__all__ = [
    "SUPPORTED_PROVIDERS",
    "_PROVIDER_ENV_KEYS",
    "ProviderConfig",
    "UnknownProviderError",
    "configured_providers",
    "default_model_for",
    "resolve_provider_config",
    "resolve_vision_provider_config",
    "persist_model",
    "persist_provider_keys",
    "model_choices_from_config",
]
