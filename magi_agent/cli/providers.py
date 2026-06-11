"""Provider/key resolution for the local ``magi`` CLI.

The installed CLI ships a model-free stub runner (:mod:`magi_agent.cli.local_runner`)
so ``magi`` launches without any configuration. This module is the bridge to a
*real* runner: it discovers which model provider the user has configured and the
API key to use, from either a config file (``~/.magi/config.toml``, overridable
via ``MAGI_CONFIG``) or environment variables.

Four providers are supported, all routed through ADK's ``LiteLlm``:
``openai``, ``anthropic``, ``gemini`` and ``fireworks``.

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
# the rest. Also the set of accepted ``provider`` values.
SUPPORTED_PROVIDERS: tuple[str, ...] = ("anthropic", "openai", "gemini", "fireworks")

# Env var(s) carrying the API key for each provider, in lookup order.
_PROVIDER_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "fireworks": ("FIREWORKS_API_KEY",),
}

# Default model id per provider, overridable via config ``[model].model`` or the
# ``MAGI_MODEL`` env var. Model ids drift over time; treat these as a best-effort
# starting point and override when a provider retires a name.
_DEFAULT_MODEL: dict[str, str] = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-5.5",
    "gemini": "gemini-3.5-flash",
    "fireworks": "accounts/fireworks/models/kimi-k2-instruct",
}

# litellm provider prefix per provider (``<prefix>/<model>``).
_LITELLM_PREFIX: dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "gemini": "gemini",
    "fireworks": "fireworks_ai",
}


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


def resolve_provider_config(
    *,
    model_override: str | None = None,
    env: Mapping[str, str] | None = None,
    config: Mapping[str, object] | None = None,
) -> ProviderConfig | None:
    """Resolve the active provider config, or ``None`` if none is configured.

    ``env`` and ``config`` are injectable for testing; they default to the real
    process environment and ``~/.magi/config.toml``.
    """

    env = os.environ if env is None else env
    config = _load_config_file() if config is None else config

    model_section = _section(config, "model")
    providers_section = _section(config, "providers")

    def key_for(provider: str) -> str | None:
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

    def model_for(provider: str) -> str:
        return (
            _clean_model(model_override)
            or _clean_model(env.get("MAGI_MODEL"))
            or _clean_model(model_section.get("model"))
            or _DEFAULT_MODEL[provider]
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
    "ProviderConfig",
    "UnknownProviderError",
    "default_model_for",
    "resolve_provider_config",
    "resolve_vision_provider_config",
    "persist_model",
    "model_choices_from_config",
]
