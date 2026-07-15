"""Read-only coverage audit for every effect-capable Magi Agent surface.

The discovery pass deliberately does not import tool handlers or runtime wiring.
It parses the installed source tree, inventories manifest/entrypoint declarations,
pins the live admission topology, and finds direct effect primitives.  The golden
inventory is loaded separately so deleting an entry cannot hide a newly discovered
surface.

This module is a conformance ratchet only.  It does not register with, attach, or
otherwise activate the universal broker.
"""

from __future__ import annotations

import ast
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


_DIGEST_PREFIX = "sha256:"
_FIXTURE_NAME = "effect_inventory_v1.json"
_EXEMPTION_ENFORCEMENTS = {
    "hard_reject",
    "kernel_internal_scoped",
    "read_only_source_audit",
}
_RESOLUTION_KINDS = {"broker_registration", "exemption"}
_BYPASS_DISPOSITIONS = {"broker_registration", "hard_reject"}


class EffectInventoryError(ValueError):
    """The reviewed effect inventory is malformed or self-inconsistent."""


@dataclass(frozen=True, order=True)
class EffectSurface:
    surface_id: str
    source_path: str
    symbol: str
    detector: str
    primitive: str
    category: str
    fingerprint: str
    handler_digest: str


@dataclass(frozen=True)
class EffectRegistration:
    registration_id: str
    owner: str
    category: str
    effect_class: str
    authority_profile: str
    resource_deriver: str
    source_path: str
    symbol: str
    handler_digest: str


@dataclass(frozen=True)
class EffectExemption:
    exemption_id: str
    owner: str
    reason: str
    enforcement: str
    scope: tuple[str, ...]


@dataclass(frozen=True)
class EffectResolution:
    kind: Literal["broker_registration", "exemption"]
    resolution_id: str


@dataclass(frozen=True)
class BypassCase:
    case_id: str
    category: str
    disposition: Literal["broker_registration", "hard_reject"]
    resolution_id: str
    example: str


@dataclass(frozen=True)
class EffectInventory:
    schema_version: int
    source_root: str
    registrations: tuple[EffectRegistration, ...]
    exemptions: tuple[EffectExemption, ...]
    surfaces: tuple[EffectSurface, ...]
    resolutions: tuple[tuple[str, EffectResolution], ...]
    bypass_cases: tuple[BypassCase, ...]

    def resolution_map(self) -> dict[str, EffectResolution]:
        return dict(self.resolutions)


@dataclass(frozen=True)
class EffectCoverageReport:
    discovered: tuple[EffectSurface, ...]
    missing: tuple[str, ...]
    stale: tuple[str, ...]
    duplicates: tuple[str, ...]
    handler_digest_drift: tuple[str, ...]
    invalid_resolutions: tuple[str, ...]


@dataclass(frozen=True)
class _ParsedSource:
    source_path: str
    tree: ast.Module
    definitions: dict[str, ast.AST]


@dataclass(frozen=True)
class _RawSurface:
    source_path: str
    symbol: str
    detector: str
    primitive: str
    category: str
    fingerprint: str
    handler_digest: str


_MANDATORY_BOUNDARIES: tuple[tuple[str, str, str, str], ...] = (
    (
        "magi_agent/tools/dispatcher.py",
        "ToolDispatcher._dispatch_inner",
        "adapter",
        "registered_tool_dispatch",
    ),
    (
        "magi_agent/tools/permission.py",
        "ToolPermissionPolicy._decide",
        "adapter",
        "tool_permission_policy",
    ),
    (
        "magi_agent/tools/safety.py",
        "RuntimePermissionArbiter.decide",
        "adapter",
        "runtime_permission_arbiter",
    ),
    (
        "magi_agent/tools/safety.py",
        "_preflight",
        "adapter",
        "path_shell_safety_preflight",
    ),
    (
        "magi_agent/tools/safety.py",
        "_read_ledger_preflight",
        "adapter",
        "read_ledger_preflight",
    ),
    (
        "magi_agent/gates/gate5b_full_toolhost.py",
        "Gate5BFullToolHost.dispatch",
        "adapter",
        "gate5b_dispatch",
    ),
    (
        "magi_agent/gates/gate5b_full_toolhost.py",
        "Gate5BFullToolHost._preflight_legacy_tool",
        "adapter",
        "gate5b_legacy_preflight",
    ),
    (
        "magi_agent/firstparty/packs/gates_policy_default/impl.py",
        "permission_preflight_policy",
        "adapter",
        "default_pack_permission_adapter",
    ),
)


_RISK_IMPORT_PREFIXES: tuple[tuple[str, str, str], ...] = (
    ("subprocess", "process.import", "shell_python"),
    ("multiprocessing", "process.import", "child"),
    ("sqlite3", "database.import", "database"),
    ("sqlalchemy", "database.import", "database"),
    ("psycopg", "database.import", "database"),
    ("httpx", "network.import", "http_provider"),
    ("requests", "network.import", "http_provider"),
    ("aiohttp", "network.import", "http_provider"),
    ("urllib.request", "network.import", "http_provider"),
    ("socket", "network.import", "http_provider"),
    ("websockets", "network.import", "http_provider"),
    ("mcp", "mcp.import", "mcp_custom"),
    ("google.adk.tools.mcp_tool", "mcp.import", "mcp_custom"),
    ("playwright", "browser.import", "browser"),
    ("selenium", "browser.import", "browser"),
    ("browser_use", "browser.import", "browser"),
    ("docker", "infra.import", "infra"),
    ("kubernetes", "infra.import", "infra"),
    ("boto3", "network.import", "http_provider"),
    ("google.cloud", "network.import", "http_provider"),
)

_PATH_METHODS = {
    "chmod",
    "hardlink_to",
    "lchmod",
    "mkdir",
    "open",
    "read_bytes",
    "read_text",
    "rename",
    "replace",
    "rmdir",
    "symlink_to",
    "touch",
    "unlink",
    "write_bytes",
    "write_text",
}
_OS_FILE_METHODS = {
    "chmod",
    "chown",
    "link",
    "listdir",
    "lstat",
    "makedirs",
    "mkdir",
    "open",
    "read",
    "remove",
    "removedirs",
    "rename",
    "renames",
    "replace",
    "rmdir",
    "scandir",
    "stat",
    "symlink",
    "truncate",
    "unlink",
    "walk",
    "write",
}
_SHUTIL_METHODS = {
    "chown",
    "copy",
    "copy2",
    "copyfile",
    "copyfileobj",
    "copymode",
    "copystat",
    "copytree",
    "make_archive",
    "move",
    "rmtree",
    "unpack_archive",
}
_PROCESS_EXACT = {
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
    "os.execv",
    "os.execve",
    "os.execvp",
    "os.execvpe",
    "os.kill",
    "os.killpg",
    "os.popen",
    "os.spawnl",
    "os.spawnle",
    "os.spawnlp",
    "os.spawnlpe",
    "os.spawnv",
    "os.spawnve",
    "os.spawnvp",
    "os.spawnvpe",
    "os.system",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.run",
}
_NETWORK_METHODS = {
    "connect",
    "delete",
    "download",
    "fetch",
    "get",
    "patch",
    "post",
    "put",
    "request",
    "send",
    "stream",
    "upload",
}
_DATABASE_METHODS = {
    "commit",
    "delete",
    "execute",
    "executemany",
    "executescript",
    "insert",
    "rollback",
    "update",
    "upsert",
}
_HOOK_METHODS = {"dispatch", "emit", "execute", "invoke", "run", "trigger"}
_CHILD_METHODS = {
    "create_task",
    "dispatch",
    "execute",
    "run",
    "run_async",
    "spawn",
    "start",
}
_MESSAGE_METHODS = {
    "ack",
    "delete_webhook",
    "deliver",
    "edit_message",
    "publish",
    "send",
    "send_message",
    "set_webhook",
}


def load_expected_effect_inventory(path: Path | None = None) -> EffectInventory:
    """Load and strictly validate the reviewed golden without source discovery."""

    inventory_path = path or Path(__file__).resolve().parents[1] / "fixtures" / _FIXTURE_NAME
    try:
        payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EffectInventoryError(f"cannot load effect inventory: {exc}") from exc
    return _parse_inventory(payload)


def discover_effect_surfaces(*, source_root: Path | None = None) -> tuple[EffectSurface, ...]:
    """Discover effect-capable surfaces without importing runtime modules."""

    root = (source_root or Path(__file__).resolve().parents[3]).resolve()
    package_root = root / "magi_agent"
    if not package_root.is_dir():
        raise EffectInventoryError(f"source root has no magi_agent package: {root}")

    parsed = _parse_sources(root, package_root)
    raw: list[_RawSurface] = []
    for source in parsed.values():
        visitor = _EffectVisitor(source, parsed)
        visitor.visit(source.tree)
        raw.extend(visitor.surfaces)
    raw.extend(_discover_mandatory_boundaries(parsed))
    return _finalize_surfaces(raw)


def audit_effect_coverage(
    *,
    expected: EffectInventory | None = None,
    source_root: Path | None = None,
) -> EffectCoverageReport:
    """Compare independent AST discovery with the exact reviewed inventory."""

    expected_inventory = expected or load_expected_effect_inventory()
    discovered = discover_effect_surfaces(source_root=source_root)
    expected_by_id = {surface.surface_id: surface for surface in expected_inventory.surfaces}
    discovered_by_id = {surface.surface_id: surface for surface in discovered}

    duplicate_ids = _duplicates(surface.surface_id for surface in discovered)
    duplicate_ids.update(_duplicates(surface.surface_id for surface in expected_inventory.surfaces))
    duplicate_ids.update(
        _duplicates(reg.registration_id for reg in expected_inventory.registrations)
    )
    duplicate_ids.update(_duplicates(ex.exemption_id for ex in expected_inventory.exemptions))

    missing = tuple(sorted(set(discovered_by_id).difference(expected_by_id)))
    stale = tuple(sorted(set(expected_by_id).difference(discovered_by_id)))
    drift = tuple(
        sorted(
            surface_id
            for surface_id in set(expected_by_id).intersection(discovered_by_id)
            if expected_by_id[surface_id] != discovered_by_id[surface_id]
        )
    )
    invalid = _invalid_resolutions(expected_inventory)
    return EffectCoverageReport(
        discovered=discovered,
        missing=missing,
        stale=stale,
        duplicates=tuple(sorted(duplicate_ids)),
        handler_digest_drift=drift,
        invalid_resolutions=invalid,
    )


def inventory_document_for_surfaces(surfaces: tuple[EffectSurface, ...]) -> dict[str, Any]:
    """Build a reviewable bootstrap document; audit never calls this helper."""

    registrations: dict[str, dict[str, Any]] = {}
    serialized_surfaces: list[dict[str, Any]] = []
    for surface in surfaces:
        registration_id = _registration_id(surface)
        registrations.setdefault(
            registration_id,
            {
                "id": registration_id,
                "owner": _owner_for(surface),
                "category": surface.category,
                "effectClass": _effect_class_for(surface.category),
                "authorityProfile": f"{surface.category}_v1",
                "resourceDeriver": f"{surface.category}_resources_v1",
                "sourcePath": surface.source_path,
                "symbol": surface.symbol,
                "handlerDigest": surface.handler_digest,
            },
        )
        serialized_surfaces.append(
            {
                "id": surface.surface_id,
                "sourcePath": surface.source_path,
                "symbol": surface.symbol,
                "detector": surface.detector,
                "primitive": surface.primitive,
                "category": surface.category,
                "fingerprint": surface.fingerprint,
                "handlerDigest": surface.handler_digest,
                "resolution": {
                    "kind": "broker_registration",
                    "id": registration_id,
                },
            }
        )

    missing_bypass_categories = {
        category
        for _, category, _ in _BYPASS_SENTINELS
        if category not in {str(item["category"]) for item in registrations.values()}
    }
    exemptions = [
        {
            "id": "exemption:effect-inventory-source-read",
            "owner": "execution-authority",
            "reason": (
                "The dormant auditor may only read Python source and its own golden; "
                "it cannot dispatch, publish, or mutate runtime state."
            ),
            "enforcement": "read_only_source_audit",
            "scope": [
                "magi_agent/execution_authority/adapters/tool_manifest.py",
                "magi_agent/execution_authority/fixtures/effect_inventory_v1.json",
            ],
        },
        *(
            {
                "id": f"exemption:hard-reject:{category}",
                "owner": "execution-authority",
                "reason": f"No reviewed {category} adapter exists; dispatch must fail closed.",
                "enforcement": "hard_reject",
                "scope": [f"effect-category:{category}"],
            }
            for category in sorted(missing_bypass_categories)
        ),
    ]
    return {
        "schemaVersion": 1,
        "sourceRoot": "magi_agent",
        "registrations": [registrations[key] for key in sorted(registrations)],
        "exemptions": exemptions,
        "surfaces": serialized_surfaces,
        "bypassCases": _default_bypass_cases(registrations),
    }


class _DefinitionCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.stack: list[str] = []
        self.definitions: dict[str, ast.AST] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.definitions[".".join(self.stack)] = node
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        self.definitions[".".join(self.stack)] = node
        self.generic_visit(node)
        self.stack.pop()


class _EffectVisitor(ast.NodeVisitor):
    def __init__(self, source: _ParsedSource, all_sources: dict[str, _ParsedSource]) -> None:
        self.source = source
        self.all_sources = all_sources
        self.stack: list[str] = []
        self.aliases: dict[str, str] = {}
        self.surfaces: list[_RawSurface] = []

    @property
    def symbol(self) -> str:
        return ".".join(self.stack) if self.stack else "<module>"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            self.aliases[local] = alias.name
            risk = _risk_import(alias.name)
            if risk is not None:
                primitive, category = risk
                self._add(node, "risk_import", f"{primitive}:{alias.name}", category)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                continue
            local = alias.asname or alias.name
            qualified = f"{module}.{alias.name}" if module else alias.name
            self.aliases[local] = qualified
        risk = _risk_import(module)
        if risk is not None:
            primitive, category = risk
            imported = ",".join(sorted(alias.name for alias in node.names))
            self._add(node, "risk_import", f"{primitive}:{module}:{imported}", category)

    def visit_Call(self, node: ast.Call) -> None:
        call_name = _resolve_alias(_dotted_name(node.func), self.aliases)
        if _leaf(call_name) == "ToolManifest":
            tool_name = _keyword_string(node, "name")
            if tool_name:
                category = _category_for(
                    self.source.source_path,
                    self.symbol,
                    f"tool_manifest:{tool_name}",
                )
                self._add(
                    node,
                    "tool_manifest",
                    f"tool_manifest:{tool_name}",
                    category,
                )
        elif _leaf(call_name).endswith("HookManifest"):
            hook_name = _keyword_string(node, "name") or "dynamic"
            self._add(node, "hook_manifest", f"hook_manifest:{hook_name}", "hook")

        if _leaf(call_name) == "bind_handler":
            tool_name = _positional_string(node, 0) or "dynamic"
            self._add(
                node,
                "handler_binding",
                f"handler_binding:{tool_name}",
                _category_for(self.source.source_path, self.symbol, tool_name),
            )

        effect = _effect_call(call_name, self.source.source_path, self.symbol)
        if effect is not None:
            primitive, category = effect
            self._add(node, "effect_call", primitive, category)
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        literal = _literal_string_dict(node)
        entrypoint = literal.get("entrypoint")
        name = literal.get("name")
        if entrypoint and name:
            category = _category_for(self.source.source_path, self.symbol, f"{name} {entrypoint}")
            target_digest = _entrypoint_digest(entrypoint, self.all_sources)
            fingerprint = _digest_ast(node)
            self.surfaces.append(
                _RawSurface(
                    source_path=self.source.source_path,
                    symbol=self.symbol,
                    detector="plugin_entrypoint",
                    primitive=f"plugin_entrypoint:{name}:{entrypoint}",
                    category=category,
                    fingerprint=fingerprint,
                    handler_digest=target_digest or fingerprint,
                )
            )
        self.generic_visit(node)

    def _add(self, node: ast.AST, detector: str, primitive: str, category: str) -> None:
        fingerprint = _digest_ast(node)
        definition = self.source.definitions.get(self.symbol)
        self.surfaces.append(
            _RawSurface(
                source_path=self.source.source_path,
                symbol=self.symbol,
                detector=detector,
                primitive=primitive,
                category=category,
                fingerprint=fingerprint,
                handler_digest=_digest_ast(definition) if definition is not None else fingerprint,
            )
        )


def _parse_sources(root: Path, package_root: Path) -> dict[str, _ParsedSource]:
    parsed: dict[str, _ParsedSource] = {}
    for path in sorted(package_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source_path = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=source_path)
        except (OSError, SyntaxError, UnicodeError) as exc:
            raise EffectInventoryError(f"cannot parse {source_path}: {exc}") from exc
        collector = _DefinitionCollector()
        collector.visit(tree)
        parsed[source_path] = _ParsedSource(source_path, tree, collector.definitions)
    return parsed


def _discover_mandatory_boundaries(
    parsed: dict[str, _ParsedSource],
) -> list[_RawSurface]:
    surfaces: list[_RawSurface] = []
    for source_path, symbol, category, primitive in _MANDATORY_BOUNDARIES:
        source = parsed.get(source_path)
        if source is None:
            continue
        node = source.definitions.get(symbol)
        if node is None:
            continue
        digest = _digest_ast(node)
        surfaces.append(
            _RawSurface(
                source_path=source_path,
                symbol=symbol,
                detector="mandatory_boundary",
                primitive=primitive,
                category=category,
                fingerprint=digest,
                handler_digest=digest,
            )
        )
    return surfaces


def _finalize_surfaces(raw: list[_RawSurface]) -> tuple[EffectSurface, ...]:
    grouped: dict[tuple[str, str, str], list[_RawSurface]] = {}
    for item in raw:
        symbol_key = item.symbol if item.detector == "mandatory_boundary" else "*"
        grouped.setdefault((item.source_path, symbol_key, item.category), []).append(item)
    surfaces: list[EffectSurface] = []
    for key, items in grouped.items():
        source_path, symbol_key, category = key
        symbols = tuple(sorted({item.symbol for item in items}))
        symbol = symbols[0] if len(symbols) == 1 else f"<aggregate:{len(symbols)} symbols>"
        if symbol_key != "*":
            symbol = symbol_key
        primitives = tuple(sorted({item.primitive for item in items}))
        detectors = tuple(sorted({item.detector for item in items}))
        handler_digests = tuple(sorted({item.handler_digest for item in items}))
        material = "\n".join((*symbols, *primitives, *detectors, *handler_digests))
        fingerprint = _DIGEST_PREFIX + hashlib.sha256(material.encode()).hexdigest()
        surface_id = "surface:" + hashlib.sha256(
            ":".join((source_path, symbol_key, category)).encode()
        ).hexdigest()[:24]
        surfaces.append(
            EffectSurface(
                surface_id=surface_id,
                source_path=source_path,
                symbol=symbol,
                detector="+".join(detectors),
                primitive="+".join(primitives),
                category=category,
                fingerprint=fingerprint,
                handler_digest=handler_digests[0],
            )
        )
    return tuple(sorted(surfaces))


def _risk_import(module: str) -> tuple[str, str] | None:
    for prefix, primitive, category in _RISK_IMPORT_PREFIXES:
        if module == prefix or module.startswith(prefix + "."):
            return primitive, category
    return None


def _effect_call(call_name: str, source_path: str, symbol: str) -> tuple[str, str] | None:
    leaf = _leaf(call_name)
    lower_name = call_name.lower()
    context = f"{source_path} {symbol} {call_name}".lower()

    if call_name in _PROCESS_EXACT or call_name.startswith("subprocess."):
        return f"process.{leaf.lower()}", "shell_python"
    if call_name in {"threading.Thread", "multiprocessing.Process"}:
        return "process.spawn", "child"
    if call_name in {"open", "io.open", "os.fdopen", "pathlib.Path.open"}:
        return "filesystem.open", "filesystem"
    if leaf in _PATH_METHODS and not lower_name.startswith(("zipfile.", "tarfile.")):
        return f"path.{leaf}", "filesystem"
    if call_name.startswith("os.") and leaf in _OS_FILE_METHODS:
        return f"filesystem.os_{leaf}", "filesystem"
    if call_name.startswith("shutil.") and leaf in _SHUTIL_METHODS:
        return f"filesystem.shutil_{leaf}", "filesystem"
    if call_name.startswith("tempfile."):
        return f"filesystem.tempfile_{leaf.lower()}", "filesystem"
    if call_name.startswith(("httpx.", "requests.", "aiohttp.", "urllib.request.")):
        return f"network.{leaf.lower()}", _category_for(source_path, symbol, call_name)
    if call_name.startswith(("socket.", "websockets.")):
        return f"network.{leaf.lower()}", "http_provider"
    if "mcp" in context and leaf in _NETWORK_METHODS | {"call_tool", "execute", "invoke"}:
        return f"mcp.{leaf.lower()}", "mcp_custom"
    if _has_any(context, ("browser", "playwright", "selenium")) and leaf in {
        "click",
        "close",
        "connect",
        "fill",
        "goto",
        "launch",
        "navigate",
        "press",
        "run",
        "start",
        "type",
    }:
        return f"browser.{leaf.lower()}", "browser"
    if _has_any(context, ("sqlite", "database", "/storage/", "_store", "ledger")) and (
        leaf in _DATABASE_METHODS
    ):
        return f"database.{leaf.lower()}", "database"
    if _has_any(context, ("hook", "callback")) and leaf in _HOOK_METHODS:
        return f"hook.{leaf.lower()}", "hook"
    if _has_any(context, ("subagent", "child_", "child.", "spawn_agent", "deep_solve")) and (
        leaf in _CHILD_METHODS or "spawn" in leaf
    ):
        return f"child.{leaf.lower()}", "child"
    if _has_any(context, ("scheduler", "scheduled", "cron", "work_queue", "background_task")) and (
        leaf in _DATABASE_METHODS | _CHILD_METHODS | {"enqueue", "schedule", "submit"}
    ):
        return f"scheduler.{leaf.lower()}", "scheduler"
    if "mission" in context and leaf in _DATABASE_METHODS | {"advance", "apply", "record"}:
        return f"mission.{leaf.lower()}", "mission"
    if "memory" in context and leaf in _DATABASE_METHODS | {"append", "remember", "save", "write"}:
        return f"memory.{leaf.lower()}", "memory"
    if _has_any(context, ("knowledge", "/kb", "okf")) and leaf in _DATABASE_METHODS | {
        "append",
        "save",
        "write",
    }:
        return f"knowledge.{leaf.lower()}", "knowledge"
    if "artifact" in context and leaf in _NETWORK_METHODS | _DATABASE_METHODS | {
        "deliver",
        "save",
        "write",
    }:
        return f"artifact.{leaf.lower()}", "artifact"
    if _has_any(context, ("channel", "gateway", "message", "telegram", "slack", "discord", "email")) and (
        leaf in _MESSAGE_METHODS
    ):
        return f"message.{leaf.lower()}", "message"
    if _has_any(context, ("provider", "connector", "web_acquisition", "composio", "apify")) and (
        leaf in _NETWORK_METHODS | {"call", "execute", "invoke"}
    ):
        return f"network.{leaf.lower()}", "http_provider"
    if _has_any(context, ("service_install", "kubernetes", "docker", "infra")) and leaf in (
        _PROCESS_EXACT | _DATABASE_METHODS | {"apply", "create", "delete", "install", "start", "stop"}
    ):
        return f"infra.{leaf.lower()}", "infra"
    if "git" in context and leaf in {"apply", "checkout", "clone", "commit", "push", "run"}:
        return f"git.{leaf.lower()}", "git"
    if call_name == "asyncio.create_task":
        return "scheduler.create_task", _category_for(source_path, symbol, call_name)
    return None


def _category_for(source_path: str, symbol: str, primitive: str) -> str:
    text = f"{source_path} {symbol} {primitive}".lower()
    if _has_any(text, ("subagent", "child", "spawnagent", "deep_solve")):
        return "child"
    if "hook" in text or "callback" in text:
        return "hook"
    if _has_any(text, ("scheduler", "scheduled", "cron", "work_queue", "background_task")):
        return "scheduler"
    if "mission" in text:
        return "mission"
    if "memory" in text:
        return "memory"
    if _has_any(text, ("knowledge", "kb", "okf")):
        return "knowledge"
    if "artifact" in text or "documentwrite" in text or "spreadsheetwrite" in text:
        return "artifact"
    if _has_any(text, ("telegram", "slack", "discord", "email", "message", "channel", "gateway")):
        return "message"
    if "browser" in text or "computer" in text:
        return "browser"
    if "mcp" in text or "custom_tool" in text or "external_tool" in text:
        return "mcp_custom"
    if _has_any(text, ("infra", "kubernetes", "docker", "service_install")):
        return "infra"
    if "git" in text:
        return "git"
    if _has_any(text, ("sqlite", "database", "storage", "ledger", "store")):
        return "database"
    if _has_any(text, ("http", "web", "provider", "connector", "composio", "apify", "network")):
        return "http_provider"
    if _has_any(text, ("bash", "shell", "python", "process", "testrun", "command")):
        return "shell_python"
    if _has_any(text, ("file", "path", "workspace", "document", "archive")):
        return "filesystem"
    if "adapter" in text or "dispatch" in text or "permission" in text or "preflight" in text:
        return "adapter"
    return "adapter"


def _parse_inventory(payload: object) -> EffectInventory:
    document = _require_mapping(payload, "inventory")
    schema_version = _require_int(document, "schemaVersion")
    if schema_version != 1:
        raise EffectInventoryError("schemaVersion must equal 1")
    source_root = _require_nonempty(document, "sourceRoot")

    registrations = tuple(
        _parse_registration(item, index)
        for index, item in enumerate(_require_list(document, "registrations"))
    )
    exemptions = tuple(
        _parse_exemption(item, index)
        for index, item in enumerate(_require_list(document, "exemptions"))
    )
    parsed_surfaces = [
        _parse_surface(item, index)
        for index, item in enumerate(_require_list(document, "surfaces"))
    ]
    bypass_cases = tuple(
        _parse_bypass_case(item, index)
        for index, item in enumerate(_require_list(document, "bypassCases"))
    )
    surfaces = tuple(sorted(surface for surface, _ in parsed_surfaces))
    resolutions = tuple(sorted((surface.surface_id, resolution) for surface, resolution in parsed_surfaces))

    inventory = EffectInventory(
        schema_version=schema_version,
        source_root=source_root,
        registrations=registrations,
        exemptions=exemptions,
        surfaces=surfaces,
        resolutions=resolutions,
        bypass_cases=bypass_cases,
    )
    duplicates = set()
    duplicates.update(_duplicates(item.registration_id for item in registrations))
    duplicates.update(_duplicates(item.exemption_id for item in exemptions))
    duplicates.update(_duplicates(item.surface_id for item in surfaces))
    duplicates.update(_duplicates(item.case_id for item in bypass_cases))
    if duplicates:
        raise EffectInventoryError(f"duplicate inventory ids: {', '.join(sorted(duplicates))}")
    invalid = _invalid_resolutions(inventory)
    if invalid:
        raise EffectInventoryError(f"invalid resolutions: {', '.join(invalid)}")
    return inventory


def _parse_registration(payload: object, index: int) -> EffectRegistration:
    item = _require_mapping(payload, f"registrations[{index}]")
    return EffectRegistration(
        registration_id=_require_nonempty(item, "id"),
        owner=_require_nonempty(item, "owner"),
        category=_require_nonempty(item, "category"),
        effect_class=_require_nonempty(item, "effectClass"),
        authority_profile=_require_nonempty(item, "authorityProfile"),
        resource_deriver=_require_nonempty(item, "resourceDeriver"),
        source_path=_require_nonempty(item, "sourcePath"),
        symbol=_require_nonempty(item, "symbol"),
        handler_digest=_require_digest(item, "handlerDigest"),
    )


def _parse_exemption(payload: object, index: int) -> EffectExemption:
    item = _require_mapping(payload, f"exemptions[{index}]")
    owner = _require_nonempty(item, "owner")
    reason = _require_nonempty(item, "reason")
    enforcement = _require_nonempty(item, "enforcement")
    if enforcement not in _EXEMPTION_ENFORCEMENTS:
        raise EffectInventoryError(f"exemptions[{index}].enforcement is not mechanically enforced")
    scope = tuple(_require_string_list(item, "scope"))
    if not scope:
        raise EffectInventoryError(f"exemptions[{index}].scope must be non-empty")
    return EffectExemption(
        exemption_id=_require_nonempty(item, "id"),
        owner=owner,
        reason=reason,
        enforcement=enforcement,
        scope=scope,
    )


def _parse_surface(payload: object, index: int) -> tuple[EffectSurface, EffectResolution]:
    item = _require_mapping(payload, f"surfaces[{index}]")
    resolution_payload = _require_mapping(item.get("resolution"), f"surfaces[{index}].resolution")
    kind = _require_nonempty(resolution_payload, "kind")
    if kind not in _RESOLUTION_KINDS:
        raise EffectInventoryError(f"surfaces[{index}].resolution.kind is invalid")
    resolution = EffectResolution(
        kind=kind,  # type: ignore[arg-type]
        resolution_id=_require_nonempty(resolution_payload, "id"),
    )
    return (
        EffectSurface(
            surface_id=_require_nonempty(item, "id"),
            source_path=_require_nonempty(item, "sourcePath"),
            symbol=_require_nonempty(item, "symbol"),
            detector=_require_nonempty(item, "detector"),
            primitive=_require_nonempty(item, "primitive"),
            category=_require_nonempty(item, "category"),
            fingerprint=_require_digest(item, "fingerprint"),
            handler_digest=_require_digest(item, "handlerDigest"),
        ),
        resolution,
    )


def _parse_bypass_case(payload: object, index: int) -> BypassCase:
    item = _require_mapping(payload, f"bypassCases[{index}]")
    disposition = _require_nonempty(item, "disposition")
    if disposition not in _BYPASS_DISPOSITIONS:
        raise EffectInventoryError(f"bypassCases[{index}].disposition is invalid")
    return BypassCase(
        case_id=_require_nonempty(item, "id"),
        category=_require_nonempty(item, "category"),
        disposition=disposition,  # type: ignore[arg-type]
        resolution_id=_require_nonempty(item, "resolutionId"),
        example=_require_nonempty(item, "example"),
    )


def _invalid_resolutions(inventory: EffectInventory) -> tuple[str, ...]:
    registrations = {item.registration_id for item in inventory.registrations}
    exemptions = {item.exemption_id for item in inventory.exemptions}
    invalid: list[str] = []
    resolution_map = inventory.resolution_map()
    for surface in inventory.surfaces:
        resolution = resolution_map.get(surface.surface_id)
        if resolution is None:
            invalid.append(f"{surface.surface_id}:missing")
        elif resolution.kind == "broker_registration":
            if resolution.resolution_id not in registrations:
                invalid.append(f"{surface.surface_id}:unknown-registration")
        elif resolution.resolution_id not in exemptions:
            invalid.append(f"{surface.surface_id}:unknown-exemption")
    for case in inventory.bypass_cases:
        target = registrations if case.disposition == "broker_registration" else exemptions
        if case.resolution_id not in target:
            invalid.append(f"bypass:{case.case_id}:unknown-resolution")
    return tuple(sorted(invalid))


def _entrypoint_digest(entrypoint: str, parsed: dict[str, _ParsedSource]) -> str | None:
    module, separator, symbol = entrypoint.partition(":")
    if not separator or not module.startswith("magi_agent.") or not symbol:
        return None
    source_path = module.replace(".", "/") + ".py"
    source = parsed.get(source_path)
    if source is None:
        package_init = module.replace(".", "/") + "/__init__.py"
        source = parsed.get(package_init)
    if source is None:
        return None
    node = source.definitions.get(symbol)
    return _digest_ast(node) if node is not None else None


def _registration_id(surface: EffectSurface) -> str:
    material = f"{surface.source_path}:{surface.symbol}:{surface.category}"
    suffix = hashlib.sha256(material.encode()).hexdigest()[:16]
    return f"registration:{surface.category}:{suffix}"


def _owner_for(surface: EffectSurface) -> str:
    relative = surface.source_path.removeprefix("magi_agent/")
    top = relative.split("/", 1)[0].removesuffix(".py")
    return f"magi-agent/{top}"


def _effect_class_for(category: str) -> str:
    return {
        "artifact": "artifact.deliver",
        "browser": "browser.act",
        "child": "child.execute",
        "database": "database.mutate",
        "filesystem": "workspace.access",
        "git": "workspace.git",
        "hook": "hook.execute",
        "http_provider": "network.egress",
        "infra": "infra.mutate",
        "knowledge": "knowledge.access",
        "mcp_custom": "mcp.dispatch",
        "memory": "memory.access",
        "message": "message.deliver",
        "mission": "mission.mutate",
        "scheduler": "scheduler.mutate",
        "shell_python": "process.execute",
    }.get(category, "adapter.dispatch")


_BYPASS_SENTINELS = (
    ("patch_apply", "filesystem", "PatchApply envelope"),
    ("shell_cp", "shell_python", "Bash: cp source target"),
    ("shell_touch", "shell_python", "Bash: touch target"),
    ("shell_redirection", "shell_python", "Bash: printf x > target"),
    ("inline_python", "shell_python", "PythonExec / python -c"),
    ("mcp_custom_dispatch", "mcp_custom", "external MCP or custom tool"),
    ("hook_execution", "hook", "operator command hook"),
    ("scheduler_write", "scheduler", "scheduled work mutation"),
    ("memory_write", "memory", "MemoryWrite"),
    ("kb_write", "knowledge", "knowledge base mutation"),
    ("artifact_delivery", "artifact", "artifact/document delivery"),
    ("message_delivery", "message", "channel message delivery"),
    ("http_provider_call", "http_provider", "provider HTTP request"),
    ("browser_action", "browser", "browser click/navigation"),
    ("infra_action", "infra", "service or infrastructure mutation"),
    ("child_execution", "child", "SpawnAgent / child runner"),
)


def _default_bypass_cases(registrations: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    by_category: dict[str, str] = {}
    for registration_id, registration in registrations.items():
        by_category.setdefault(str(registration["category"]), registration_id)

    return [
        {
            "id": case_id,
            "category": category,
            "disposition": (
                "broker_registration" if category in by_category else "hard_reject"
            ),
            "resolutionId": by_category.get(category, f"exemption:hard-reject:{category}"),
            "example": example,
        }
        for case_id, category, example in _BYPASS_SENTINELS
    ]


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return "<dynamic>"


def _resolve_alias(name: str, aliases: dict[str, str]) -> str:
    head, separator, tail = name.partition(".")
    resolved = aliases.get(head, head)
    return f"{resolved}.{tail}" if separator else resolved


def _leaf(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def _digest_ast(node: ast.AST | None) -> str:
    if node is None:
        return _DIGEST_PREFIX + hashlib.sha256(b"missing").hexdigest()
    material = ast.dump(node, annotate_fields=True, include_attributes=False)
    return _DIGEST_PREFIX + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _keyword_string(node: ast.Call, name: str) -> str | None:
    for keyword in node.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return keyword.value.value if isinstance(keyword.value.value, str) else None
    return None


def _positional_string(node: ast.Call, index: int) -> str | None:
    if len(node.args) <= index:
        return None
    argument = node.args[index]
    if not isinstance(argument, ast.Constant):
        return None
    value = argument.value
    return value if isinstance(value, str) else None


def _literal_string_dict(node: ast.Dict) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in zip(node.keys, node.values, strict=True):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            result[key.value] = value.value
    return result


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _duplicates(values: Any) -> set[str]:
    counts = Counter(values)
    return {str(value) for value, count in counts.items() if count > 1}


def _require_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise EffectInventoryError(f"{label} must be an object")
    return value


def _require_list(value: dict[str, object], key: str) -> list[object]:
    item = value.get(key)
    if not isinstance(item, list):
        raise EffectInventoryError(f"{key} must be a list")
    return item


def _require_nonempty(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise EffectInventoryError(f"{key} must be a non-empty string")
    return item


def _require_int(value: dict[str, object], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool):
        raise EffectInventoryError(f"{key} must be an integer")
    return item


def _require_digest(value: dict[str, object], key: str) -> str:
    item = _require_nonempty(value, key)
    suffix = item.removeprefix(_DIGEST_PREFIX)
    if not item.startswith(_DIGEST_PREFIX) or len(suffix) != 64:
        raise EffectInventoryError(f"{key} must be a sha256 digest")
    try:
        int(suffix, 16)
    except ValueError as exc:
        raise EffectInventoryError(f"{key} must be a sha256 digest") from exc
    return item


def _require_string_list(value: dict[str, object], key: str) -> list[str]:
    item = value.get(key)
    if not isinstance(item, list) or any(
        not isinstance(entry, str) or not entry.strip() for entry in item
    ):
        raise EffectInventoryError(f"{key} must be a list of non-empty strings")
    return item
