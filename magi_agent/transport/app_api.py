"""Dashboard ``/v1/app/*`` API surface.

The committed static dashboard bundle (``magi_agent/web_dashboard``) is built
from ``apps/web`` and talks to a ``/v1/app/*`` API family for its Overview,
Usage, Skills, Memory, Knowledge and Settings pages. Those routes never existed
on the runtime, so every one of those pages 404'd with "Failed to load local
runtime". This module implements the contract the bundle expects.

Design notes
------------
* The runtime (:class:`OpenMagiRuntime`) is a thin shell — it owns the tool
  registry and config but no session/task/cron/artifact managers. Endpoints
  surface real data where the runtime exposes it (tools, skills, config, and
  workspace files) and a valid-but-empty projection (``count: 0``/``items: []``)
  where a subsystem is genuinely not wired. A fresh local runtime legitimately
  has zero sessions/tasks/crons/artifacts, which is what the dashboard shows.
* All reads are fail-soft: a missing DB or unreadable file yields empty rather
  than a 500, mirroring the app's fail-open posture elsewhere.
* The workspace root is an explicit workspace env var when provided, falling
  back to the process cwd used by local ``magi-agent serve`` sessions.
* File reads/writes are confined to the workspace and refuse sealed operator
  files and secret-looking names.
"""

from __future__ import annotations

import os
import re
import stat
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from magi_agent.plugins.native.skills import _skill_candidates
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.transport.tools import _unauthorized_response

# Operator-owned files that must never be overwritten through the dashboard.
_SEALED_BASENAMES = {
    "SOUL.md",
    "TOOLS.md",
    "AGENTS.md",
    "CLAUDE.md",
    "HEARTBEAT.md",
}
# Basenames that look like they carry secrets — never read or written here.
_SECRET_NAME_RE = re.compile(
    r"(^\.env)|secret|credential|password|api[_-]?key|token", re.IGNORECASE
)

# Self-identity files read into the system prompt by
# ``magi_agent.cli.identity.load_identity`` from the ``.magi`` namespace
# (``~/.magi`` global + ``<workspace>/.magi`` project override). Surfaced
# read-only in the Memory dashboard so what feeds the prompt is visible.
# ``AGENTS.md`` is deliberately excluded: it is a sealed cross-tool convention
# file (see ``_SEALED_BASENAMES``) and stays hidden here.
_IDENTITY_BASENAMES = ("IDENTITY.md", "USER.md", "BOOTSTRAP.md", "LEARNING.md")
# Synthetic path prefix used in API responses for files in the global ``~/.magi``
# namespace (which lives outside any workspace root).
_GLOBAL_IDENTITY_PREFIX = "~/.magi/"

# The runtime's fixed skill-hook points (see plugins/native/skills.py).
_RUNTIME_HOOK_POINTS = ("beforeModelCall", "afterToolCall", "beforeCommit", "afterTurnEnd")

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_MAX_SEARCH_BYTES = 200_000
_PREVIEW_CHARS = 240
_SCRIPT_SUFFIXES = {".sh", ".py", ".js", ".ts"}
_WORKSPACE_ENV_VARS = (
    "MAGI_AGENT_WORKSPACE",
    "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT",
    "CORE_AGENT_WORKSPACE_ROOT",
)
_HOSTED_LEGACY_WORKSPACE_RELATIVES = (
    Path("workspace"),
    Path("open" + "claw-home") / "workspace",
    Path("agents") / "main" / "workspace",
)
_WORKSPACE_FILE_STATE_MARKERS = (
    "MEMORY.md",
    "USER.md",
    "memory",
    ".magi/memory",
    "knowledge",
    ".magi/knowledge",
)
_WORKSPACE_STATE_MARKERS = (
    *_WORKSPACE_FILE_STATE_MARKERS,
    "skills",
    ".magi/skills",
    "docs/superpowers",
)


class _ConfigValidationError(ValueError):
    """Validation error for dashboard config writes."""

    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.error = error


# --------------------------------------------------------------------------- #
# Workspace + path safety helpers
# --------------------------------------------------------------------------- #
def _workspace_root() -> Path:
    env_root = _workspace_root_from_env()
    if env_root is not None:
        return env_root
    return Path(os.getcwd()).resolve()


def _workspace_root_from_env() -> Path | None:
    for name in _WORKSPACE_ENV_VARS:
        value = os.environ.get(name)
        if value and value.strip():
            return Path(value.strip()).expanduser().resolve()
    return None


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _workspace_root_has_state(root: Path, markers: tuple[str, ...]) -> bool:
    return any(_path_exists(root / marker) for marker in markers)


def _path_has_content(path: Path) -> bool:
    try:
        if path.is_file():
            return True
        if path.is_dir():
            return any(path.iterdir())
    except OSError:
        return False
    return False


def _workspace_root_has_content(root: Path, markers: tuple[str, ...]) -> bool:
    return any(_path_has_content(root / marker) for marker in markers)


def _workspace_roots() -> list[Path]:
    """Return primary workspace root plus known hosted legacy fallbacks."""
    primary = _workspace_root()
    roots = [primary]
    if _workspace_root_from_env() is None:
        return roots
    seen = {primary}
    for relative in _HOSTED_LEGACY_WORKSPACE_RELATIVES:
        candidate = (primary / relative).resolve()
        if candidate in seen:
            continue
        try:
            candidate.relative_to(primary)
        except ValueError:
            continue
        if not _workspace_root_has_state(candidate, _WORKSPACE_STATE_MARKERS):
            continue
        roots.append(candidate)
        seen.add(candidate)
    return roots


def _workspace_write_root() -> Path:
    roots = _workspace_roots()
    for root in roots:
        if _workspace_root_has_content(root, _WORKSPACE_FILE_STATE_MARKERS):
            return root
    for root in roots:
        if _workspace_root_has_state(root, _WORKSPACE_FILE_STATE_MARKERS):
            return root
    return roots[0]


def _resolve_in_root(root: Path, relative: str) -> Path | None:
    candidate = (root / relative.lstrip("/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _resolve_in_workspace(relative: str) -> Path | None:
    """Resolve ``relative`` under the workspace, blocking traversal escapes."""
    fallback: Path | None = None
    for root in _workspace_roots():
        candidate = _resolve_in_root(root, relative)
        if candidate is None:
            return None
        if fallback is None:
            fallback = candidate
        if _path_exists(candidate):
            return candidate
    return fallback


def _resolve_in_workspace_for_write(relative: str) -> Path | None:
    return _resolve_in_root(_workspace_write_root(), relative)


def _is_protected(path: Path) -> bool:
    name = path.name
    return name in _SEALED_BASENAMES or bool(_SECRET_NAME_RE.search(name))


def _is_archive_memory(path: Path | None) -> bool:
    """``memory/archive/`` holds pre-compaction snapshots (compaction history).

    They are surfaced read-only in the dashboard: editing or deleting them would
    corrupt the tree's audit trail, so writes/deletes to anything under a
    workspace ``memory/archive/`` directory are refused. Read/list stay allowed.
    """
    if path is None:
        return False
    for root in _workspace_roots():
        try:
            rel = path.resolve().relative_to(root.resolve()).as_posix()
        except (OSError, ValueError):
            continue
        if rel == "memory/archive" or rel.startswith("memory/archive/"):
            return True
    return False


def _global_magi_dir() -> Path:
    """The global ``~/.magi`` namespace (mirrors ``cli.identity.load_identity``)."""
    return Path(os.path.expanduser("~")) / ".magi"


def _list_identity_files() -> list[dict[str, Any]]:
    """List the self-identity files from the ``.magi`` namespace.

    Surfaces the global ``~/.magi`` files (path ``~/.magi/<name>``) and any
    per-workspace ``<root>/.magi`` overrides (path ``.magi/<name>``). Mirrors
    the resolution order of ``cli.identity.load_identity`` so the Memory
    dashboard shows exactly what feeds the system prompt. Sealed/secret-shaped
    basenames are skipped via ``_is_protected``.
    """
    out: list[dict[str, Any]] = []
    seen_api: set[str] = set()
    seen_fs: set[Path] = set()

    def add(api_path: str, fs_path: Path) -> None:
        if api_path in seen_api or not fs_path.is_file() or _is_protected(fs_path):
            return
        try:
            resolved = fs_path.resolve()
        except OSError:
            return
        if resolved in seen_fs:
            return
        seen_api.add(api_path)
        seen_fs.add(resolved)
        out.append({"path": api_path, **_file_stat(fs_path)})

    global_dir = _global_magi_dir()
    for name in _IDENTITY_BASENAMES:
        add(f"{_GLOBAL_IDENTITY_PREFIX}{name}", global_dir / name)
    for root in _workspace_roots():
        for name in _IDENTITY_BASENAMES:
            add(f".magi/{name}", root / ".magi" / name)
    return out


def _resolve_identity_file(api_path: str) -> Path | None:
    """Resolve a global ``~/.magi/<name>`` identity path to a filesystem path.

    Project-scoped ``.magi/<name>`` paths live inside the workspace and are
    resolved by ``_resolve_in_workspace``; this handler covers only the global
    namespace, restricted to the known identity basenames.
    """
    if not api_path.startswith(_GLOBAL_IDENTITY_PREFIX):
        return None
    name = api_path[len(_GLOBAL_IDENTITY_PREFIX) :]
    if name not in _IDENTITY_BASENAMES:
        return None
    candidate = _global_magi_dir() / name
    if _is_protected(candidate):
        return None
    return candidate


def _is_identity_file(path: Path | None) -> bool:
    """Self-identity files are surfaced read-only: never deleted from here."""
    if path is None:
        return False
    return path.name in _IDENTITY_BASENAMES and path.parent.name == ".magi"


def _file_stat(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"sizeBytes": stat.st_size, "mtimeMs": int(stat.st_mtime * 1000)}


def _read_text(path: Path, *, limit: int | None = None) -> str | None:
    try:
        data = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return data if limit is None else data[:limit]


# --------------------------------------------------------------------------- #
# Runtime status
# --------------------------------------------------------------------------- #
def _session_items(runtime: OpenMagiRuntime) -> list[dict[str, Any]]:
    """Best-effort read of persisted sessions. Never raises, never creates a DB."""
    try:
        from magi_agent.storage.session_store import (
            SessionSqliteStore,
            SessionStoreConfig,
        )

        config = SessionStoreConfig(enabled=True)
        store = SessionSqliteStore(config, workspace_root=str(_workspace_root()))
        if not store.db_full_path.exists():
            return []
        rows = store.list_sync(app_name="magi", user_id=runtime.config.user_id)
        items: list[dict[str, Any]] = []
        for row in rows:
            state = row.get("state") if isinstance(row.get("state"), dict) else {}
            usage = None
            try:
                usage = store.get_usage_sync(row["id"])
            except Exception:  # noqa: BLE001 - usage is optional
                usage = None
            item: dict[str, Any] = {
                "sessionKey": row["id"],
                "persona": state.get("persona"),
                "channel": state.get("channel"),
                "lastActivityAt": row.get("updated_at"),
            }
            if usage is not None:
                item["budget"] = {
                    "turns": usage.turn_count,
                    "inputTokens": usage.tokens_in,
                    "outputTokens": usage.tokens_out,
                    "costUsd": usage.cost_usd,
                }
            items.append(item)
        store.close()
        return items
    except Exception:  # noqa: BLE001 - fail-soft: dashboard must still load
        return []


def _runtime_snapshot(runtime: OpenMagiRuntime) -> dict[str, Any]:
    skills = _scan_skills()
    tool_count = len(runtime.tool_registry.list_all())
    sessions = _session_items(runtime)
    return {
        "sessions": {"count": len(sessions), "items": sessions},
        # Not wired into the thin runtime shell yet — honestly empty.
        "tasks": {"count": 0, "items": []},
        "crons": {"count": 0, "internalCount": 0, "items": []},
        "artifacts": {"count": 0, "items": []},
        "tools": {"count": tool_count, "skillCount": len(skills["loaded"])},
        "skills": {
            "loadedCount": len(skills["loaded"]),
            "count": len(skills["loaded"]),
            "issueCount": len(skills["issues"]),
            "runtimeHookCount": len(skills["runtimeHooks"]),
        },
    }


# --------------------------------------------------------------------------- #
# Skills
# --------------------------------------------------------------------------- #
def _parse_frontmatter(text: str) -> dict[str, str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip().strip("'\"")
    return fields


def _scan_skills() -> dict[str, Any]:
    loaded: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    seen: set[str] = set()

    for root in _workspace_roots():
        for relative in _skill_candidates(root):
            ref = _skill_reference(root, relative)
            if ref is None or ref["dir"] in seen:
                continue
            seen.add(ref["dir"])
            text = _read_skill_text(ref)
            if text is None:
                issues.append(
                    {"dir": ref["dir"], "path": ref["path"], "reason": "unreadable"}
                )
                continue
            front = _parse_frontmatter(text)
            tags = [t.strip() for t in front.get("tags", "").split(",") if t.strip()]
            script_backed = _skill_has_script(ref)
            loaded.append(
                {
                    "name": front.get("name") or Path(ref["dir"]).name,
                    "dir": ref["dir"],
                    "path": ref["path"],
                    "source": ref["source"],
                    "description": front.get("description", ""),
                    "tags": tags,
                    "promptOnly": not script_backed,
                    "scriptBacked": script_backed,
                    "runtimeHooks": 0,
                }
            )

    runtime_hooks = [
        {"name": point, "point": point, "kind": "builtin", "source": "runtime"}
        for point in _RUNTIME_HOOK_POINTS
    ]
    return {
        "loaded": loaded,
        "issues": issues,
        "runtimeHooks": runtime_hooks,
        "issueCount": len(issues),
        "runtimeHookCount": len(runtime_hooks),
        "loadedCount": len(loaded),
    }


def _skill_reference(root: Path, relative: str) -> dict[str, Any] | None:
    path_parts = Path(relative).parts
    if not path_parts or path_parts[-1] != "SKILL.md":
        return None
    if relative.startswith("bundled/"):
        try:
            resource = resources.files("magi_agent").joinpath("skills", *path_parts)
        except (FileNotFoundError, ModuleNotFoundError):
            return None
        return {
            "dir": Path(relative).parent.as_posix(),
            "path": relative,
            "source": "bundled",
            "resource": resource,
            "filesystem_path": None,
        }
    legacy_prefix = "legacy-workspace/skills/"
    if relative.startswith(legacy_prefix):
        if root.name != "workspace" or root.parent.name != "workspace":
            return None
        inner = relative[len(legacy_prefix):]
        path = root.parent / "skills" / inner
        return {
            "dir": Path(relative).parent.as_posix(),
            "path": path.as_posix(),
            "source": "legacy_workspace",
            "resource": None,
            "filesystem_path": path,
        }
    path = root / relative
    return {
        "dir": Path(relative).parent.as_posix(),
        "path": path.as_posix(),
        "source": "workspace",
        "resource": None,
        "filesystem_path": path,
    }


def _read_skill_text(ref: dict[str, Any]) -> str | None:
    resource = ref.get("resource")
    if isinstance(resource, Traversable):
        try:
            text = resource.read_text(encoding="utf-8")
        except (FileNotFoundError, UnicodeDecodeError, OSError):
            return None
        return text[:_MAX_SEARCH_BYTES]
    path = ref.get("filesystem_path")
    if isinstance(path, Path):
        return _read_text(path, limit=_MAX_SEARCH_BYTES)
    return None


def _skill_has_script(ref: dict[str, Any]) -> bool:
    path = ref.get("filesystem_path")
    if isinstance(path, Path):
        skill_dir = path.parent
        try:
            return any(
                p.is_file() and p.suffix in _SCRIPT_SUFFIXES
                for p in skill_dir.rglob("*")
            )
        except OSError:
            return False
    resource = ref.get("resource")
    if isinstance(resource, Traversable):
        return _traversable_has_script(resource.parent)
    return False


def _traversable_has_script(node: Traversable) -> bool:
    try:
        children = list(node.iterdir())
    except (FileNotFoundError, OSError):
        return False
    for child in children:
        if child.is_file() and Path(child.name).suffix in _SCRIPT_SUFFIXES:
            return True
        if child.is_dir() and _traversable_has_script(child):
            return True
    return False


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def _config_snapshot(runtime: OpenMagiRuntime) -> dict[str, Any]:
    from magi_agent.cli import providers

    raw = providers._load_config_file()
    model_section = providers._section(raw, "model")
    resolved = None
    try:
        resolved = providers.resolve_provider_config(config=raw)
    except Exception:  # noqa: BLE001 - bad config must not 500 the page
        resolved = None

    provider = (
        resolved.provider
        if resolved
        else _canonical_provider(model_section.get("provider"), strict=False)
    )
    model = resolved.model if resolved else providers._clean(model_section.get("model"))
    api_key_set = bool(
        (resolved and resolved.api_key) or providers._clean(model_section.get("api_key"))
    )

    return {
        "ok": True,
        "exists": providers._config_path().exists(),
        "config": {
            "llm": {
                "provider": provider,
                "model": model,
                "baseUrl": providers._clean(model_section.get("base_url")),
                "apiKeySet": api_key_set,
                "apiKeyEnvVar": providers._clean(model_section.get("api_key_env")),
            },
            "server": {
                "gatewayTokenSet": bool(runtime.config.gateway_token),
            },
            "workspace": str(_workspace_root()),
        },
    }


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _canonical_provider(value: object, *, strict: bool = True) -> str | None:
    from magi_agent.cli import providers

    provider = providers._clean(value)
    if provider is None:
        return None
    normalized = provider.lower()
    if normalized == "google":
        return "gemini"
    if normalized in providers.SUPPORTED_PROVIDERS:
        return normalized
    if strict:
        raise _ConfigValidationError("unsupported_provider")
    return normalized


def _write_config(payload: dict[str, Any]) -> None:
    """Write model-selection keys to config.toml in a merge-preserving way.

    Loads the existing config, updates ONLY the ``[model]`` keys it manages
    (provider/model/base_url/api_key/api_key_env), and leaves ``[providers.*]``
    and any other sections untouched.  Uses the same round-trip self-check +
    atomic temp-file pattern as :func:`~magi_agent.cli.providers.persist_model`.
    """
    import tomllib

    from magi_agent.cli import providers

    llm = payload.get("llm") if isinstance(payload.get("llm"), dict) else {}
    existing = providers._load_config_file()
    existing_model = providers._section(existing, "model")
    existing_provider = _canonical_provider(existing_model.get("provider"), strict=False)
    next_provider = _canonical_provider(llm.get("provider"))
    next_api_key = providers._clean(llm.get("apiKey"))
    if next_api_key is None and next_provider == existing_provider:
        next_api_key = providers._clean(existing_model.get("api_key"))

    # Build the new [model] section by updating only the managed keys.
    new_model: dict[str, object] = dict(existing_model)  # preserve unmanaged keys
    managed: list[tuple[str, object]] = [
        ("provider", next_provider),
        ("model", llm.get("model")),
        ("base_url", llm.get("baseUrl")),
        ("api_key", next_api_key),
        ("api_key_env", llm.get("apiKeyEnvVar")),
    ]
    for key, value in managed:
        if isinstance(value, str) and value.strip():
            new_model[key] = value.strip()
        else:
            new_model.pop(key, None)

    # Merge into the full config dict, preserving all other sections.
    raw = dict(existing)  # shallow copy top level
    if new_model:
        raw["model"] = new_model
    else:
        raw.pop("model", None)

    # Round-trip self-check before writing (mirrors persist_model).
    rendered = providers._render_toml(raw)
    reparsed = tomllib.loads(rendered)
    if reparsed != raw:
        raise ValueError(
            "TOML round-trip self-check failed: the rendered config does not "
            "re-parse to the intended dict. Aborting to preserve the original file."
        )

    path = providers._config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".toml.tmp")
    try:
        # Create the temp file with 0600 from the start so the secret is never
        # briefly world-readable at the default umask (M3 defense-in-depth).
        fd = os.open(
            tmp_path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            stat.S_IRUSR | stat.S_IWUSR,  # 0o600
        )
        try:
            os.write(fd, rendered.encode("utf-8"))
        finally:
            os.close(fd)
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
def _providers_snapshot() -> dict[str, Any]:
    """Build the GET /v1/app/providers response — NEVER leaks key values."""
    from magi_agent.cli import providers

    raw = providers._load_config_file()
    providers_cfg = providers._section(raw, "providers")
    model_section = providers._section(raw, "model")
    active = _canonical_provider(model_section.get("provider"), strict=False)

    # configured_providers reads both env and config (config passed explicitly,
    # env defaults to os.environ), so the dashboard reflects env-configured
    # providers too. Only a boolean is surfaced — never a key value.
    configured = set(providers.configured_providers(config=raw))

    items: list[dict[str, Any]] = []
    for name in providers.SUPPORTED_PROVIDERS:
        provider_block = providers_cfg.get(name)
        stored_model: str | None = None
        if isinstance(provider_block, dict):
            stored_model = providers._clean(provider_block.get("model"))
        env_keys = providers._PROVIDER_ENV_KEYS.get(name, ())
        items.append(
            {
                "name": name,
                "configured": name in configured,
                "model": stored_model or providers.default_model_for(name),
                "envVar": env_keys[0] if env_keys else None,
            }
        )
    return {"providers": items, "active": active}


# --------------------------------------------------------------------------- #
# Workspace file domains (memory + knowledge)
# --------------------------------------------------------------------------- #
def _list_markdown(rel_dirs: list[str], extra_files: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(root: Path, path: Path) -> None:
        if not path.is_file() or _is_protected(path):
            return
        try:
            path.resolve().relative_to(root)
            rel = path.relative_to(root).as_posix()
        except (OSError, ValueError):
            return
        if rel in seen:
            return
        seen.add(rel)
        out.append({"path": rel, **_file_stat(path)})

    for root in _workspace_roots():
        for name in extra_files:
            add(root, root / name)
        for rel_dir in rel_dirs:
            base = root / rel_dir
            if base.is_dir():
                for path in sorted(base.rglob("*.md")):
                    add(root, path)
    return out


def _search_files(files: list[dict[str, Any]], query: str, limit: int) -> list[dict[str, Any]]:
    needle = query.lower()
    results: list[dict[str, Any]] = []
    if not needle:
        return results
    for entry in files:
        rel = str(entry["path"])
        path = _resolve_identity_file(rel) or _resolve_in_workspace(rel)
        if path is None:
            continue
        text = _read_text(path, limit=_MAX_SEARCH_BYTES)
        if text is None:
            continue
        idx = text.lower().find(needle)
        if idx < 0:
            continue
        start = max(0, idx - 80)
        context = text[start : start + _PREVIEW_CHARS]
        results.append(
            {
                "path": entry["path"],
                "score": 1.0,
                "context": context,
                "contentPreview": text[:_PREVIEW_CHARS],
            }
        )
        if len(results) >= limit:
            break
    return results


def _vector_memory_search(query: str, limit: int) -> list[dict[str, Any]] | None:
    """Semantic search over the workspace ``memory/`` tree via qmd vsearch.

    Returns ``None`` when vector search is not usable (operator opt-in OFF, qmd
    binary absent, or the per-workspace collection has not been embedded yet) so
    the caller can fall back to the substring matcher.  Returns a (possibly
    empty) list of result dicts — same shape as :func:`_search_files` — when the
    vector backend ran.

    This is an EXPLICIT, latency-tolerant surface: ``qmd vsearch`` cold-loads the
    embedding model (~10-40s), which is why it is gated to this dashboard endpoint
    and never the per-turn recall hot path.  Fail-soft: any error → ``None``.
    """
    if not query.strip():
        return None
    try:
        from magi_agent.memory.config import resolve_memory_config
        from magi_agent.memory.search import select_search_backend

        config = resolve_memory_config()
        if not config.vector_search:
            return None
        backend = select_search_backend(config, vector=True)
        if not backend.capabilities.supports_vector:
            # qmd absent (fell back to PyBM25, no vector) -> let caller use substring.
            return None
        root = _workspace_root()
        backend.reindex(root)
        hits = backend.search(query, k=max(int(limit), 1))
    except Exception:  # noqa: BLE001 - never break the endpoint on a search error
        return None
    results: list[dict[str, Any]] = []
    for hit in hits:
        path = getattr(hit, "path", None)
        content = getattr(hit, "content", None)
        score = getattr(hit, "score", None)
        if not isinstance(path, str) or not isinstance(content, str):
            continue
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            continue
        results.append(
            {
                "path": path,
                "score": float(score),
                "context": content[:_PREVIEW_CHARS],
                "contentPreview": content[:_PREVIEW_CHARS],
            }
        )
    return results


# --------------------------------------------------------------------------- #
# Route registration
# --------------------------------------------------------------------------- #
def register_app_api_routes(app: FastAPI, runtime: OpenMagiRuntime) -> None:
    """Mount the dashboard ``/v1/app/*`` API surface."""

    def guard(request: Request) -> JSONResponse | None:
        return _unauthorized_response(request, runtime)

    # ---- runtime -------------------------------------------------------- #
    @app.get("/v1/app/runtime")
    def app_runtime(request: Request) -> JSONResponse:
        denied = guard(request)
        if denied is not None:
            return denied
        return JSONResponse(content=_runtime_snapshot(runtime))

    # ---- config --------------------------------------------------------- #
    @app.get("/v1/app/config")
    def app_config_get(request: Request) -> JSONResponse:
        denied = guard(request)
        if denied is not None:
            return denied
        return JSONResponse(content=_config_snapshot(runtime))

    @app.put("/v1/app/config")
    async def app_config_put(request: Request) -> JSONResponse:
        denied = guard(request)
        if denied is not None:
            return denied
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        try:
            _write_config(payload if isinstance(payload, dict) else {})
        except _ConfigValidationError as exc:
            return JSONResponse(status_code=400, content={"error": exc.error})
        except OSError as exc:
            return JSONResponse(status_code=500, content={"error": str(exc)})
        return JSONResponse(content=_config_snapshot(runtime))

    # ---- providers ------------------------------------------------------ #
    @app.get("/v1/app/providers")
    def app_providers_get(request: Request) -> JSONResponse:
        denied = guard(request)
        if denied is not None:
            return denied
        return JSONResponse(content=_providers_snapshot())

    @app.put("/v1/app/providers")
    async def app_providers_put(request: Request) -> JSONResponse:
        from magi_agent.cli import providers as _providers
        from magi_agent.cli.providers import UnknownProviderError

        denied = guard(request)
        if denied is not None:
            return denied
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=400, content={"error": "invalid_json"})

        if not isinstance(payload, dict):
            payload = {}

        raw_providers = payload.get("providers")
        raw_active = payload.get("active")

        if not isinstance(raw_providers, dict):
            raw_providers = {}

        # Build key-update map and per-provider model updates.
        key_updates: dict[str, str | None] = {}
        model_updates: dict[str, str] = {}  # provider → model
        for name, block in raw_providers.items():
            if not isinstance(block, dict):
                continue
            api_key = block.get("apiKey")
            key_updates[name] = api_key if isinstance(api_key, str) else None
            model_val = _providers._clean(block.get("model"))
            if model_val:
                model_updates[name] = model_val

        active: str | None = None
        if isinstance(raw_active, str) and raw_active.strip():
            try:
                active = _canonical_provider(raw_active)
            except _ConfigValidationError as exc:
                return JSONResponse(status_code=400, content={"error": exc.error})

        try:
            # Pass model_updates through persist_provider_keys so keys AND models
            # are written in a single atomic 0600 write (fixes C2/M1/M2).
            _providers.persist_provider_keys(
                key_updates,
                active=active,
                models=model_updates if model_updates else None,
            )
        except UnknownProviderError as exc:
            return JSONResponse(
                status_code=400, content={"error": "unsupported_provider", "detail": str(exc)}
            )
        except OSError as exc:
            return JSONResponse(status_code=500, content={"error": str(exc)})

        return JSONResponse(content=_providers_snapshot())

    # ---- skills --------------------------------------------------------- #
    @app.get("/v1/app/skills")
    def app_skills(request: Request) -> JSONResponse:
        denied = guard(request)
        if denied is not None:
            return denied
        return JSONResponse(content=_scan_skills())

    @app.post("/v1/app/skills/reload")
    def app_skills_reload(request: Request) -> JSONResponse:
        denied = guard(request)
        if denied is not None:
            return denied
        # The scan does fresh disk I/O each call; "reload" is just a re-scan.
        return JSONResponse(content=_scan_skills())

    # ---- memory --------------------------------------------------------- #
    @app.get("/v1/app/memory")
    def app_memory(request: Request) -> JSONResponse:
        denied = guard(request)
        if denied is not None:
            return denied
        files = _list_markdown(["memory", ".magi/memory"], ["MEMORY.md", "USER.md"])
        files = _list_identity_files() + files
        return JSONResponse(content={"files": files})

    @app.get("/v1/app/memory/file")
    def app_memory_file(request: Request, path: str) -> JSONResponse:
        denied = guard(request)
        if denied is not None:
            return denied
        target = _resolve_identity_file(path) or _resolve_in_workspace(path)
        if target is None or _is_protected(target):
            return JSONResponse(status_code=403, content={"error": "forbidden_path"})
        content = _read_text(target)
        if content is None:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        return JSONResponse(content={"content": content})

    @app.delete("/v1/app/memory/files")
    async def app_memory_delete(request: Request) -> JSONResponse:
        denied = guard(request)
        if denied is not None:
            return denied
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        paths = payload.get("paths") if isinstance(payload, dict) else None
        if not isinstance(paths, list):
            return JSONResponse(status_code=400, content={"error": "paths_required"})
        deleted: list[str] = []
        for rel in paths:
            if not isinstance(rel, str):
                continue
            target = _resolve_in_workspace(rel)
            if (
                target is None
                or _is_protected(target)
                or _is_archive_memory(target)
                or _is_identity_file(target)
                or not target.is_file()
            ):
                continue
            try:
                target.unlink()
                deleted.append(rel)
            except OSError:
                continue
        return JSONResponse(content={"deleted": deleted})

    @app.get("/v1/app/memory/search")
    def app_memory_search(
        request: Request, q: str = "", limit: int = 15, vector: int = 0
    ) -> JSONResponse:
        denied = guard(request)
        if denied is not None:
            return denied
        # Opt-in semantic search (qmd vsearch over the memory/ tree). Falls back to
        # the substring matcher when vector is off/unavailable so the endpoint's
        # contract is unchanged for existing callers.
        if vector:
            vector_results = _vector_memory_search(q, limit)
            if vector_results is not None:
                return JSONResponse(
                    content={"results": vector_results, "mode": "vector"}
                )
        files = _list_markdown(["memory", ".magi/memory"], ["MEMORY.md", "USER.md"])
        files = _list_identity_files() + files
        return JSONResponse(
            content={"results": _search_files(files, q, limit), "mode": "substring"}
        )

    # ---- workspace file write ------------------------------------------ #
    @app.put("/v1/app/workspace/file")
    async def app_workspace_write(request: Request) -> JSONResponse:
        denied = guard(request)
        if denied is not None:
            return denied
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        rel = payload.get("path") if isinstance(payload, dict) else None
        content = payload.get("content") if isinstance(payload, dict) else None
        if not isinstance(rel, str) or not isinstance(content, str):
            return JSONResponse(status_code=400, content={"error": "path_and_content_required"})
        target = _resolve_in_workspace_for_write(rel)
        if target is None or _is_protected(target) or _is_archive_memory(target):
            return JSONResponse(status_code=403, content={"error": "forbidden_path"})
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return JSONResponse(status_code=500, content={"error": str(exc)})
        return JSONResponse(content={"path": rel})

    # ---- knowledge ------------------------------------------------------ #
    @app.get("/v1/app/knowledge")
    def app_knowledge(request: Request, collection: str | None = None) -> JSONResponse:
        denied = guard(request)
        if denied is not None:
            return denied
        return JSONResponse(content=_knowledge_index(collection))

    @app.get("/v1/app/knowledge/file")
    def app_knowledge_file(request: Request, path: str) -> JSONResponse:
        denied = guard(request)
        if denied is not None:
            return denied
        target = _resolve_in_workspace(path)
        if target is None or _is_protected(target):
            return JSONResponse(status_code=403, content={"error": "forbidden_path"})
        content = _read_text(target)
        if content is None:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        return JSONResponse(content={"content": content, "path": path})

    @app.put("/v1/app/knowledge/file")
    async def app_knowledge_write(request: Request) -> JSONResponse:
        denied = guard(request)
        if denied is not None:
            return denied
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        rel = payload.get("path") if isinstance(payload, dict) else None
        content = payload.get("content") if isinstance(payload, dict) else None
        if not isinstance(rel, str) or not isinstance(content, str):
            return JSONResponse(status_code=400, content={"error": "path_and_content_required"})
        target = _resolve_in_workspace_for_write(rel)
        if target is None or _is_protected(target) or _is_archive_memory(target):
            return JSONResponse(status_code=403, content={"error": "forbidden_path"})
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return JSONResponse(status_code=500, content={"error": str(exc)})
        return JSONResponse(content={"path": rel})

    @app.get("/v1/app/knowledge/search")
    def app_knowledge_search(
        request: Request, q: str = "", limit: int = 25, collection: str | None = None
    ) -> JSONResponse:
        denied = guard(request)
        if denied is not None:
            return denied
        index = _knowledge_index(collection)
        files = [
            {"path": doc["path"]}
            for doc in index["documents"]
        ]
        hits = _search_files(files, q, limit)
        by_path = {doc["path"]: doc for doc in index["documents"]}
        results = []
        for hit in hits:
            doc = dict(by_path.get(hit["path"], {}))
            doc["score"] = hit["score"]
            doc["snippet"] = hit["context"]
            results.append(doc)
        return JSONResponse(content={"results": results})


def _knowledge_index(collection: str | None) -> dict[str, Any]:
    """Scan workspace knowledge dirs. Empty when no KB store is configured."""
    collections: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    seen_documents: set[str] = set()
    for root in _workspace_roots():
        for base_name in ("knowledge", ".magi/knowledge"):
            base = root / base_name
            if not base.is_dir():
                continue
            for coll_dir in sorted(p for p in base.iterdir() if p.is_dir()):
                if collection is not None and coll_dir.name != collection:
                    continue
                docs = [
                    p
                    for p in sorted(coll_dir.rglob("*"))
                    if p.is_file() and not _is_protected(p)
                ]
                size = sum(p.stat().st_size for p in docs)
                collections.append(
                    {
                        "name": coll_dir.name,
                        "path": coll_dir.relative_to(root).as_posix(),
                        "documentCount": len(docs),
                        "sizeBytes": size,
                    }
                )
                for doc in docs:
                    rel = doc.relative_to(root).as_posix()
                    if rel in seen_documents:
                        continue
                    seen_documents.add(rel)
                    stat = doc.stat()
                    documents.append(
                        {
                            "collection": coll_dir.name,
                            "filename": doc.name,
                            "title": doc.stem,
                            "path": rel,
                            "sizeBytes": stat.st_size,
                            "mtimeMs": int(stat.st_mtime * 1000),
                        }
                    )
    return {"collections": collections, "documents": documents}
