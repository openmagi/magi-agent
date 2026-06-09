# OSS Customize Tab Parity — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the OSS local dashboard's "runtime console" Customize tab with the hosted web app's two-card layout (Verification Rules + Custom Tools), populated from a new read-only `GET /v1/app/customize` endpoint that reflects the runtime's real primitives (recipes, harness presets, hooks, tools).

**Architecture:** A new `magi_agent/customize/` package provides (a) `store.py` to load overrides from `~/.magi/customize.json` (read-only this phase) and (b) `catalog.py` to reflect real runtime primitives into a UI-shaped catalog. A new `transport/customize.py` exposes `GET /v1/app/customize` returning `{catalog, overrides}`, guarded by the existing gateway-token auth. The Next.js Customize tab is rewritten into the hosted two-card shell, fed by that endpoint via the existing `useAgentFetch` local API client. Toggles render with client-side-only state this phase; persistence + enforcement land in Phase 2. The custom-rule wizard (hosted) is deferred to Phase 3.

**Tech Stack:** Python 3 / FastAPI / Pydantic (backend); Next.js 16 static export / React 19 / TypeScript / Tailwind (frontend); pytest + `uv` (backend tests); `tsc --noEmit` + Next build (frontend).

**Reference source (hosted, read-only, on disk — DO NOT import, adapt only):**
- `/Users/kevin/Desktop/claude_code/clawy/src/components/dashboard/customize/customize-tab.tsx`
- `/Users/kevin/Desktop/claude_code/clawy/src/components/dashboard/customize/verification-rule-modal.tsx`
- `/Users/kevin/Desktop/claude_code/clawy/src/components/dashboard/customize/custom-tool-modal.tsx`

The hosted versions persist to Supabase via `/api/bots/[botId]`. We DROP all Supabase / botId-row concepts and feed from `/v1/app/customize` instead.

---

## Data contract: `GET /v1/app/customize`

Both backend and frontend tasks depend on this exact shape. Response body:

```json
{
  "overrides": {
    "verification": { "recipes": [], "harness_presets": [], "hooks": {}, "custom_rules": [] },
    "tools": {}
  },
  "catalog": {
    "verification": {
      "recipes": [
        { "id": "research", "title": "Research", "description": "...", "category": "research", "source": "docs/recipes.md", "enabled": true }
      ],
      "harnessPresets": [
        { "id": "answer_quality", "title": "Answer Quality", "description": "...", "category": "answer", "enabled": false }
      ],
      "hooks": [
        { "name": "secret-scan", "point": "before_tool_use", "title": "secret-scan", "category": "security", "alwaysOn": true, "enabled": true }
      ]
    },
    "tools": [
      { "name": "web_fetch", "description": "...", "enabled": true, "source": "builtin", "dangerous": false }
    ]
  }
}
```

Notes:
- `overrides` is the on-disk file (empty defaults this phase).
- `catalog.*.enabled` is the *effective* state: this phase = registry defaults (overrides are empty so they don't change anything yet).
- Hook `alwaysOn` mirrors the registry's `protected` flag (builtin / native-plugin hooks the user cannot disable). `category` is `"security"` for protected hooks, else `"general"`.
- Keys are camelCase in JSON (`harnessPresets`) to match frontend TS conventions.

---

## File Structure

- Create `magi_agent/customize/__init__.py` — package marker, re-exports.
- Create `magi_agent/customize/store.py` — overrides file I/O (read-only this phase). One responsibility: locate + load + shape-normalize `customize.json`.
- Create `magi_agent/customize/catalog.py` — reflect runtime primitives into the catalog dict. One responsibility: read registries + curated recipe/preset constants → JSON-able catalog.
- Create `magi_agent/transport/customize.py` — `register_customize_routes(app, runtime)` exposing `GET /v1/app/customize`. One responsibility: HTTP surface + auth.
- Modify `magi_agent/app.py` — call `register_customize_routes` in `create_app()` (one line + import).
- Create `tests/test_customize_store.py`, `tests/test_customize_catalog.py`, `tests/test_customize_routes.py`.
- Modify `apps/web/src/components/dashboard/customize/customize-tab.tsx` — rewrite to two-card shell.
- Create `apps/web/src/components/dashboard/customize/verification-rule-modal.tsx` — presets + security list (read/visual + local toggle).
- Create `apps/web/src/components/dashboard/customize/custom-tool-modal.tsx` — tools enable/disable list (local state).
- Create `apps/web/src/lib/customize-api.ts` — typed `useCustomize()` fetch hook + TS types for the contract.
- Modify `magi_agent/web_dashboard/**` — regenerated static export (build artifact, Task 9).

---

## Task 1: Overrides store (`store.py`)

**Files:**
- Create: `magi_agent/customize/__init__.py`
- Create: `magi_agent/customize/store.py`
- Test: `tests/test_customize_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_customize_store.py
import json
from pathlib import Path

from magi_agent.customize.store import DEFAULT_OVERRIDES, customize_path, load_overrides


def test_missing_file_returns_default(tmp_path: Path) -> None:
    result = load_overrides(tmp_path / "nope.json")
    assert result == DEFAULT_OVERRIDES
    # returned value must be a copy, not the shared module constant
    result["tools"]["x"] = True
    assert "x" not in DEFAULT_OVERRIDES["tools"]


def test_malformed_json_returns_default(tmp_path: Path) -> None:
    target = tmp_path / "customize.json"
    target.write_text("{not json", encoding="utf-8")
    assert load_overrides(target) == DEFAULT_OVERRIDES


def test_partial_file_is_shape_normalized(tmp_path: Path) -> None:
    target = tmp_path / "customize.json"
    target.write_text(json.dumps({"tools": {"web_fetch": False}}), encoding="utf-8")
    result = load_overrides(target)
    assert result["tools"] == {"web_fetch": False}
    assert result["verification"] == {
        "recipes": [],
        "harness_presets": [],
        "hooks": {},
        "custom_rules": [],
    }


def test_customize_path_respects_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "c.json"))
    assert customize_path() == tmp_path / "c.json"
    monkeypatch.delenv("MAGI_CUSTOMIZE", raising=False)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "cfg" / "config.toml"))
    assert customize_path() == tmp_path / "cfg" / "customize.json"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/test_customize_store.py -q`
Expected: FAIL — `ModuleNotFoundError: magi_agent.customize`.

- [ ] **Step 3: Write minimal implementation**

```python
# magi_agent/customize/__init__.py
from magi_agent.customize.store import (
    DEFAULT_OVERRIDES,
    customize_path,
    load_overrides,
)

__all__ = ["DEFAULT_OVERRIDES", "customize_path", "load_overrides"]
```

```python
# magi_agent/customize/store.py
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

DEFAULT_OVERRIDES: dict[str, Any] = {
    "verification": {
        "recipes": [],
        "harness_presets": [],
        "hooks": {},
        "custom_rules": [],
    },
    "tools": {},
}


def customize_path() -> Path:
    """Locate customize.json beside the runtime config (env-overridable)."""
    override = os.environ.get("MAGI_CUSTOMIZE")
    if override:
        return Path(override)
    config = os.environ.get("MAGI_CONFIG")
    if config:
        return Path(config).parent / "customize.json"
    return Path.home() / ".magi" / "customize.json"


def _clone_default() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_OVERRIDES)


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    merged = _clone_default()
    verification = data.get("verification")
    if isinstance(verification, dict):
        for key in merged["verification"]:
            if key in verification and isinstance(
                verification[key], type(merged["verification"][key])
            ):
                merged["verification"][key] = verification[key]
    tools = data.get("tools")
    if isinstance(tools, dict):
        merged["tools"] = tools
    return merged


def load_overrides(path: Path | None = None) -> dict[str, Any]:
    """Load + shape-normalize the overrides file. Never raises; falls back to defaults."""
    target = path or customize_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError, OSError):
        return _clone_default()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _clone_default()
    if not isinstance(data, dict):
        return _clone_default()
    return _normalize(data)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/test_customize_store.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add magi_agent/customize/__init__.py magi_agent/customize/store.py tests/test_customize_store.py
git commit -m "feat(customize): add overrides store (read-only load)"
```

---

## Task 2: Primitive catalog (`catalog.py`)

**Files:**
- Create: `magi_agent/customize/catalog.py`
- Test: `tests/test_customize_catalog.py`

**Context for implementer:** Reflect REAL runtime primitives. Tools + hooks come from live registries; recipes + harness presets are curated constants that mirror real recipe modules under `magi_agent/recipes/first_party/` and the documented harness presets (`docs/harness-schema.md`). Before writing, open `magi_agent/transport/tools.py` and reuse its `_public_tools(runtime)` (or replicate the subset it builds) so the tool shape is consistent with `GET /api/tools`. Open `magi_agent/hooks/registry.py` and confirm the field names on the objects returned by `runtime.hook_registry.list_all()` (the explore notes: `HookRegistration(manifest, enabled, protected, ...)`, and the manifest carries the hook name + hook point). If the attribute names differ from what this task assumes (`reg.manifest.name`, `reg.manifest.point`, `reg.enabled`, `reg.protected`), adjust the mapping but keep the OUTPUT contract identical. If you cannot resolve a real attribute, report BLOCKED rather than inventing one.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_customize_catalog.py
from magi_agent.customize.catalog import (
    HARNESS_PRESETS,
    RECIPES,
    build_catalog,
)


class _FakeManifest:
    def __init__(self, name, point, description="", source="builtin", dangerous=False):
        self.name = name
        self.point = point
        self.description = description
        self.source = source
        self.dangerous = dangerous


class _FakeHookReg:
    def __init__(self, name, point, enabled, protected):
        self.manifest = _FakeManifest(name, point)
        self.enabled = enabled
        self.protected = protected


class _FakeToolReg:
    def __init__(self, name, enabled, protected=False):
        self.manifest = _FakeManifest(name, point=None, description=f"{name} desc")
        self.enabled = enabled
        self.protected = protected


class _FakeRegistry:
    def __init__(self, items):
        self._items = items

    def list_all(self):
        return list(self._items)


class _FakeRuntime:
    def __init__(self, hooks, tools):
        self.hook_registry = _FakeRegistry(hooks)
        self.tool_registry = _FakeRegistry(tools)


def test_catalog_has_curated_recipes_and_presets() -> None:
    runtime = _FakeRuntime(hooks=[], tools=[])
    catalog = build_catalog(runtime)
    assert len(catalog["verification"]["recipes"]) == len(RECIPES)
    assert len(catalog["verification"]["harnessPresets"]) == len(HARNESS_PRESETS)
    assert catalog["verification"]["recipes"][0]["id"]  # has an id


def test_protected_hook_is_always_on_security() -> None:
    runtime = _FakeRuntime(
        hooks=[
            _FakeHookReg("secret-scan", "before_tool_use", enabled=True, protected=True),
            _FakeHookReg("nudge", "after_turn_end", enabled=False, protected=False),
        ],
        tools=[],
    )
    hooks = build_catalog(runtime)["verification"]["hooks"]
    secret = next(h for h in hooks if h["name"] == "secret-scan")
    nudge = next(h for h in hooks if h["name"] == "nudge")
    assert secret["alwaysOn"] is True and secret["category"] == "security"
    assert nudge["alwaysOn"] is False and nudge["category"] == "general"
    assert nudge["enabled"] is False


def test_tools_reflect_registry() -> None:
    runtime = _FakeRuntime(
        hooks=[],
        tools=[_FakeToolReg("web_fetch", enabled=True), _FakeToolReg("shell", enabled=False)],
    )
    tools = build_catalog(runtime)["tools"]
    names = {t["name"]: t for t in tools}
    assert names["web_fetch"]["enabled"] is True
    assert names["shell"]["enabled"] is False
    assert names["web_fetch"]["description"] == "web_fetch desc"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/test_customize_catalog.py -q`
Expected: FAIL — module/attr not found.

- [ ] **Step 3: Write minimal implementation**

```python
# magi_agent/customize/catalog.py
from __future__ import annotations

from typing import Any

# Curated constants mirror REAL recipe modules under magi_agent/recipes/first_party/
# and the documented harness presets (docs/harness-schema.md). Phase 2 wires their
# selection to enforcement; Phase 1 surfaces them so the UI reaches parity.
RECIPES: list[dict[str, str]] = [
    {"id": "research", "title": "Research", "category": "research",
     "source": "docs/recipes.md",
     "description": "Multi-source research with grounded synthesis."},
    {"id": "coding_evidence_gate", "title": "Coding Evidence Gate", "category": "coding",
     "source": "magi_agent/recipes/first_party/coding",
     "description": "Require evidence before committing code changes."},
    {"id": "coding_mutation", "title": "Coding Mutation", "category": "coding",
     "source": "magi_agent/recipes/first_party/coding",
     "description": "Apply and verify workspace code mutations."},
    {"id": "general_automation", "title": "General Automation", "category": "task",
     "source": "magi_agent/recipes/first_party/general_automation",
     "description": "General multi-step task automation."},
    {"id": "memory_recall", "title": "Memory Recall", "category": "memory",
     "source": "magi_agent/recipes/first_party/memory_recall.py",
     "description": "Recall prior context from the memory ledger."},
    {"id": "self_improvement", "title": "Self Improvement", "category": "task",
     "source": "magi_agent/recipes/first_party/self_improvement.py",
     "description": "Gated self-improvement proposal loop."},
]

HARNESS_PRESETS: list[dict[str, str]] = [
    {"id": "answer_quality", "title": "Answer Quality", "category": "answer",
     "description": "Verify answers are complete and well-formed."},
    {"id": "fact_grounding", "title": "Fact Grounding", "category": "fact",
     "description": "Require factual claims to be grounded in sources."},
    {"id": "deterministic_evidence", "title": "Deterministic Evidence", "category": "fact",
     "description": "Deterministic evidence extraction for claims."},
    {"id": "coding_verification", "title": "Coding Verification", "category": "coding",
     "description": "Verify code changes against tests/build."},
    {"id": "source_authority", "title": "Source Authority", "category": "research",
     "description": "Weight sources by authority during research."},
    {"id": "hard_safety", "title": "Hard Safety", "category": "security",
     "description": "Always-on hard safety guardrails."},
]


def _recipe_entries() -> list[dict[str, Any]]:
    return [{**r, "enabled": True} for r in RECIPES]


def _preset_entries() -> list[dict[str, Any]]:
    return [{**p, "enabled": False} for p in HARNESS_PRESETS]


def _hook_entries(runtime: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for reg in runtime.hook_registry.list_all():
        manifest = reg.manifest
        name = getattr(manifest, "name")
        point = getattr(manifest, "point", None)
        protected = bool(getattr(reg, "protected", False))
        entries.append(
            {
                "name": name,
                "point": str(point) if point is not None else None,
                "title": name,
                "category": "security" if protected else "general",
                "alwaysOn": protected,
                "enabled": bool(getattr(reg, "enabled", True)),
            }
        )
    return entries


def _tool_entries(runtime: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for reg in runtime.tool_registry.list_all():
        manifest = reg.manifest
        entries.append(
            {
                "name": getattr(manifest, "name"),
                "description": getattr(manifest, "description", "") or "",
                "enabled": bool(getattr(reg, "enabled", True)),
                "source": getattr(manifest, "source", "builtin") or "builtin",
                "dangerous": bool(getattr(manifest, "dangerous", False)),
            }
        )
    return entries


def build_catalog(runtime: Any) -> dict[str, Any]:
    return {
        "verification": {
            "recipes": _recipe_entries(),
            "harnessPresets": _preset_entries(),
            "hooks": _hook_entries(runtime),
        },
        "tools": _tool_entries(runtime),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/test_customize_catalog.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Verify against the REAL registries**

Confirm the assumed attribute names exist on the real objects (not just the fakes). Quick check:

Run: `uv run python -c "import inspect, magi_agent.hooks.registry as r; print([n for n in dir(r) if 'Registration' in n or 'Manifest' in n])"`

If the real `HookRegistration`/manifest uses different attribute names (e.g. `hook_point` instead of `point`), update `_hook_entries` accordingly and re-run Task 2 tests. Keep the output contract identical.

- [ ] **Step 6: Commit**

```bash
git add magi_agent/customize/catalog.py tests/test_customize_catalog.py
git commit -m "feat(customize): reflect runtime primitives into UI catalog"
```

---

## Task 3: `GET /v1/app/customize` route

**Files:**
- Create: `magi_agent/transport/customize.py`
- Modify: `magi_agent/app.py` (register the routes in `create_app()`)
- Test: `tests/test_customize_routes.py`

**Context:** Mirror the auth + registration pattern from `magi_agent/transport/tools.py` (`_unauthorized_response(request, runtime)` checks `x-gateway-token` against `runtime.config.gateway_token`). Mirror the test pattern from `tests/test_app_api_routes.py` (build a runtime with a known `gateway_token`, `TestClient(create_app(runtime))`, set the header).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_customize_routes.py
from fastapi.testclient import TestClient

from magi_agent.app import create_app
from tests.test_app_api_routes import _build_runtime  # reuse existing runtime factory

_TOKEN = "test-token"


def _client(tmp_path):
    runtime = _build_runtime(tmp_path, gateway_token=_TOKEN)
    client = TestClient(create_app(runtime))
    return client


def test_customize_requires_auth(tmp_path) -> None:
    client = _client(tmp_path)
    resp = client.get("/v1/app/customize")
    assert resp.status_code == 401


def test_customize_returns_catalog_and_overrides(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = _client(tmp_path)
    client.headers.update({"x-gateway-token": _TOKEN})
    resp = client.get("/v1/app/customize")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"catalog", "overrides"}
    assert set(body["catalog"]) == {"verification", "tools"}
    assert set(body["catalog"]["verification"]) == {"recipes", "harnessPresets", "hooks"}
    assert body["overrides"]["tools"] == {}
```

> If `tests/test_app_api_routes.py` does not expose a reusable `_build_runtime(tmp_path, gateway_token=...)` helper, read that file and replicate its runtime-construction inline in a local `_build_runtime` here instead of importing. Do NOT change the existing test file's public surface unless it already exports such a helper.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/test_customize_routes.py -q`
Expected: FAIL — route returns 404 (not registered).

- [ ] **Step 3: Write minimal implementation**

```python
# magi_agent/transport/customize.py
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from magi_agent.customize.catalog import build_catalog
from magi_agent.customize.store import load_overrides
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.transport.tools import _unauthorized_response


def register_customize_routes(app: FastAPI, runtime: OpenMagiRuntime) -> None:
    @app.get("/v1/app/customize")
    async def get_customize(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        return JSONResponse(
            content={
                "catalog": build_catalog(runtime),
                "overrides": load_overrides(),
            }
        )
```

Then register in `magi_agent/app.py` `create_app()` — add the import near the other transport imports and the call alongside the other `register_*_routes(app, runtime)` calls:

```python
from magi_agent.transport.customize import register_customize_routes
# ... inside create_app(), next to register_tool_admin_routes(app, runtime):
register_customize_routes(app, runtime)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/test_customize_routes.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full customize backend suite + a smoke of neighbors**

Run: `uv run --extra dev pytest tests/test_customize_store.py tests/test_customize_catalog.py tests/test_customize_routes.py tests/test_app_api_routes.py -q`
Expected: PASS, no regressions in `test_app_api_routes.py`.

- [ ] **Step 6: Commit**

```bash
git add magi_agent/transport/customize.py magi_agent/app.py tests/test_customize_routes.py
git commit -m "feat(customize): add GET /v1/app/customize endpoint"
```

---

## Task 4: Frontend types + `useCustomize` hook

**Files:**
- Create: `apps/web/src/lib/customize-api.ts`
- Test: `apps/web/src/lib/customize-api.local.test.ts`

**Context:** Mirror `apps/web/src/lib/local-api.ts` (`useAgentFetch`) for the call. Read that file first to use the exact hook name/signature it exports.

- [ ] **Step 1: Write the TS types + hook**

```typescript
// apps/web/src/lib/customize-api.ts
import { useCallback, useEffect, useState } from "react";
import { useAgentFetch } from "@/lib/local-api";

export interface RecipeItem {
  id: string;
  title: string;
  description: string;
  category: string;
  source: string;
  enabled: boolean;
}
export interface HarnessPresetItem {
  id: string;
  title: string;
  description: string;
  category: string;
  enabled: boolean;
}
export interface HookItem {
  name: string;
  point: string | null;
  title: string;
  category: "security" | "general";
  alwaysOn: boolean;
  enabled: boolean;
}
export interface ToolItem {
  name: string;
  description: string;
  enabled: boolean;
  source: string;
  dangerous: boolean;
}
export interface CustomizeCatalog {
  verification: {
    recipes: RecipeItem[];
    harnessPresets: HarnessPresetItem[];
    hooks: HookItem[];
  };
  tools: ToolItem[];
}
export interface CustomizeOverrides {
  verification: {
    recipes: string[];
    harness_presets: string[];
    hooks: Record<string, boolean>;
    custom_rules: unknown[];
  };
  tools: Record<string, boolean>;
}
export interface CustomizeResponse {
  catalog: CustomizeCatalog;
  overrides: CustomizeOverrides;
}

export interface UseCustomizeResult {
  data: CustomizeResponse | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

export function useCustomize(): UseCustomizeResult {
  const agentFetch = useAgentFetch();
  const [data, setData] = useState<CustomizeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await agentFetch("/v1/app/customize");
      if (!res.ok) throw new Error(`Failed to load customize (${res.status})`);
      setData((await res.json()) as CustomizeResponse);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load customize");
    } finally {
      setLoading(false);
    }
  }, [agentFetch]);

  useEffect(() => {
    void load();
  }, [load]);

  return { data, loading, error, reload: () => void load() };
}
```

> If `useAgentFetch` is not the exact export (e.g. it's a default export or a different name/signature), adapt the import and call site to match `local-api.ts`. Keep the returned `UseCustomizeResult` shape stable — later tasks depend on it.

- [ ] **Step 2: Write a light test (pure-logic only)**

```typescript
// apps/web/src/lib/customize-api.local.test.ts
import { describe, expect, it } from "vitest";
import type { CustomizeResponse } from "./customize-api";

describe("CustomizeResponse shape", () => {
  it("accepts the documented contract", () => {
    const sample: CustomizeResponse = {
      catalog: {
        verification: { recipes: [], harnessPresets: [], hooks: [] },
        tools: [],
      },
      overrides: {
        verification: { recipes: [], harness_presets: [], hooks: {}, custom_rules: [] },
        tools: {},
      },
    };
    expect(sample.catalog.verification.hooks).toEqual([]);
  });
});
```

- [ ] **Step 3: Typecheck**

Run (in `apps/web`): `npm run check`
Expected: no type errors from the new file.

> If the repo's apps/web test runner is not vitest, mirror whatever `*.local.test.ts` uses (the explore notes existing `*.local.test.ts` files — open one to confirm the import style) and adjust the test header accordingly. If there is no JS test runner wired, drop Step 2's test and rely on `npm run check`.

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/lib/customize-api.ts apps/web/src/lib/customize-api.local.test.ts
git commit -m "feat(web): customize-api types + useCustomize hook"
```

---

## Task 5: Custom Tools modal

**Files:**
- Create: `apps/web/src/components/dashboard/customize/custom-tool-modal.tsx`

**Context:** Reference `/Users/kevin/Desktop/claude_code/clawy/src/components/dashboard/customize/custom-tool-modal.tsx` for layout, but our scope is enable/disable ONLY (NO add/delete form, NO `/api/bots` calls). Use the OSS dashboard's existing UI primitives (open `apps/web/src/components/ui/` to find the Modal/Button/Card/Toggle equivalents — match what the current `customize-tab.tsx` already imports). Data comes from the `tools: ToolItem[]` prop. Toggling updates a local `onToggle(name, enabled)` callback (no network this phase).

- [ ] **Step 1: Implement the component**

Build a modal that:
- Takes props `{ open: boolean; onClose: () => void; tools: ToolItem[]; overrides: Record<string, boolean>; onToggle: (name: string, enabled: boolean) => void }`.
- Renders two sections like hosted: a "System & Skill Tools" list (all tools), each row showing name, description, a source badge (`tool.source`), a `dangerous` badge when true, and a toggle reflecting `overrides[name] ?? tool.enabled`.
- Toggling calls `onToggle(name, next)`.
- NO add-tool form, NO delete buttons (out of scope per spec; capability additions live in the Skills tab).

Use the exact UI primitive imports that `customize-tab.tsx` currently uses (Modal/Glass/Button). Keep all user-facing strings inline (the OSS dashboard has no i18n layer like hosted — confirm by checking current `customize-tab.tsx`; if it does use one, follow it).

- [ ] **Step 2: Typecheck**

Run (in `apps/web`): `npm run check`
Expected: no type errors.

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/components/dashboard/customize/custom-tool-modal.tsx
git commit -m "feat(web): custom tools modal (enable/disable list)"
```

---

## Task 6: Verification Rules modal

**Files:**
- Create: `apps/web/src/components/dashboard/customize/verification-rule-modal.tsx`

**Context:** Reference `/Users/kevin/Desktop/claude_code/clawy/src/components/dashboard/customize/verification-rule-modal.tsx` for the categorized-preset + "Security (Always On)" layout. SCOPE for Phase 1: render-only + local toggle. EXCLUDE the custom-rule wizard (hook point → condition → check → fail behavior → preview) — that is Phase 3. Include an "Add custom rule" button that is present but disabled with a tooltip/label "Coming soon" so the screen still resembles hosted without shipping non-functional wizard steps.

- [ ] **Step 1: Implement the component**

Props: `{ open: boolean; onClose: () => void; catalog: CustomizeCatalog["verification"]; overrides: CustomizeOverrides["verification"]; onToggleHook: (name: string, enabled: boolean) => void; onToggleRecipe: (id: string, enabled: boolean) => void; onTogglePreset: (id: string, enabled: boolean) => void }`.

Render, top to bottom:
1. **Recipes** section — list `catalog.recipes`, each with title, description, category badge, toggle reflecting `overrides.recipes.includes(id) || item.enabled`.
2. **Harness Presets** section — list `catalog.harnessPresets`, toggle reflecting `overrides.harness_presets.includes(id) || item.enabled`.
3. **Hooks** section — split into "Security (Always On)" (items where `alwaysOn`) rendered as locked/non-toggleable rows, and "General" hooks rendered with toggles reflecting `overrides.hooks[name] ?? item.enabled`. Locked rows mirror hosted's always-on security list (no switch, a lock affordance).
4. **Add custom rule** button — disabled, labelled "Add custom rule (coming soon)".

Use the same UI primitives as Task 5.

- [ ] **Step 2: Typecheck**

Run (in `apps/web`): `npm run check`
Expected: no type errors.

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/components/dashboard/customize/verification-rule-modal.tsx
git commit -m "feat(web): verification rules modal (presets + always-on hooks)"
```

---

## Task 7: Rewrite Customize tab into two-card shell

**Files:**
- Modify: `apps/web/src/components/dashboard/customize/customize-tab.tsx`

**Context:** Reference `/Users/kevin/Desktop/claude_code/clawy/src/components/dashboard/customize/customize-tab.tsx` for the two-card layout. REPLACE the existing runtime-console body. The exported component name + props (e.g. `CustomizeRuntimeConsole({ botId })`) MUST stay the same so `apps/web/app/dashboard/[botId]/customize/page.tsx` keeps compiling — open that page file and keep the import/usage contract identical (rename internals only, not the export).

- [ ] **Step 1: Implement the rewrite**

The component should:
- Call `const { data, loading, error, reload } = useCustomize()`.
- Hold local override state seeded from `data.overrides` (this phase changes are not persisted; Phase 2 will POST them). Keep two modal-open booleans.
- Render a header (title "Customize", subtitle) + TWO cards (mirror hosted):
  1. **Verification Rules** card (shield icon, title, description) → opens `VerificationRuleModal`.
  2. **Custom Tools** card (wrench icon, title, description) → opens `CustomToolModal`.
- Pass `data.catalog.verification` / `data.catalog.tools` and the local override state + toggle callbacks into the modals.
- Handle `loading` (skeleton/placeholder) and `error` (inline error with a retry calling `reload`) states.
- Keep using the SAME UI primitive imports the file already used where possible.

Remove now-unused imports/helpers from the old runtime-console body (FIRST_PARTY_RECIPES / HARNESS_PRESETS constants, phase-routing panel, evidence panel, the `/api/tools` + `/v1/app/skills` + `/v1/app/config` fetches). Do not leave dead code.

- [ ] **Step 2: Typecheck**

Run (in `apps/web`): `npm run check`
Expected: no type errors across the customize components + the page.

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/components/dashboard/customize/customize-tab.tsx
git commit -m "feat(web): two-card Customize shell fed by /v1/app/customize"
```

---

## Task 8: Build static export + sync served bundle

**Files:**
- Modify: `apps/web/dist/**` (build output) and `magi_agent/web_dashboard/**` (served bundle)

**Context:** The Python runtime serves the committed static export from `magi_agent/web_dashboard/`. After the frontend changes, the bundle must be rebuilt and synced. First, find how the repo normally syncs `apps/web/dist` → `magi_agent/web_dashboard` (look for a script in `apps/web/package.json` scripts, a `scripts/` helper, or a Makefile target — e.g. a `build:dashboard` or `sync` script). Use the repo's existing sync mechanism; do NOT hand-copy if a script exists.

- [ ] **Step 1: Build**

Run (in `apps/web`): `npm install` (if not already) then `npm run build`
Expected: static export written to `apps/web/dist`, no build errors.

- [ ] **Step 2: Sync to served bundle**

Use the repo's sync script if present. If none exists, mirror the existing layout of `magi_agent/web_dashboard/` exactly (replace `_next/` assets + the dashboard HTML) — inspect the current contents first to match structure.

- [ ] **Step 3: Verify the bundle loads the new tab**

Run a quick serve smoke if feasible (e.g. `uv run magi serve` then load `/dashboard/<id>/customize`) OR at minimum confirm the built JS references the new endpoint:

Run: `grep -rl "v1/app/customize" magi_agent/web_dashboard/_next/ | head`
Expected: at least one matching built chunk.

- [ ] **Step 4: Commit**

```bash
git add apps/web/dist magi_agent/web_dashboard
git commit -m "build(web): rebuild dashboard bundle with two-card Customize tab"
```

---

## Self-Review Checklist (run before handing off)

- Spec coverage: Phase 1 = read/visual parity (two cards) + `GET /v1/app/customize` reflecting real primitives. Tasks 1–3 cover the endpoint; 4–7 the UI; 8 the served bundle. Custom-rule wizard + persistence + enforcement are explicitly Phase 2/3 (out of scope here).
- Honesty: hooks + tools are LIVE from registries; recipes + harness presets are curated constants mirroring real modules/docs. No fake-only toggles claiming enforcement that doesn't exist (this phase persists nothing; the UI must not imply changes are saved — show toggles as local/preview).
- Contract consistency: JSON uses `harnessPresets` (camel) in catalog; overrides file uses `harness_presets` (snake) matching `store.py`. Frontend types in Task 4 match both. Keep them in sync if you change either.
- Attribute-name risk: Task 2 Step 5 verifies real registry attribute names before trusting the fakes.

## Notes for the executor
- Backend tests: `uv run --extra dev pytest tests/test_customize_*.py -q`.
- Frontend typecheck: `cd apps/web && npm run check`.
- If any task is BLOCKED by an unknown real API (hook/tool manifest attrs, the dist→web_dashboard sync mechanism, the `useAgentFetch` signature), STOP and report the specific unknown rather than inventing an interface.
