# OSS Customize Tab Parity — Phase 2 Implementation Plan (Tools persistence + apply)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the Custom Tools card fully functional: toggling a tool's enable/disable PERSISTS to `~/.magi/customize.json` AND takes effect on the live runtime immediately, and is re-applied on the next runtime startup. Verification Rules (recipes/presets/hooks) stay PREVIEW-ONLY this phase (enforcing them needs runtime-engine changes deferred to a separate approved track).

**Architecture:** Extend `magi_agent/customize/store.py` with an atomic `save_overrides`. Add `magi_agent/customize/apply.py` with `apply_tool_overrides(runtime, overrides)` that calls the existing live `tool_registry.enable/disable`. Add `PATCH /v1/app/customize/tools/{name}` that persists + live-applies a single tool toggle. Call `apply_tool_overrides` once during `OpenMagiRuntime.__init__` right after the tool registry is built, so persisted choices survive restarts. Frontend wires the Custom Tools toggle to the PATCH endpoint and updates its copy to say changes are saved+applied; the Verification Rules modal keeps its preview-only messaging.

**Tech Stack:** Python/FastAPI/Pydantic; pytest+uv; Next.js static export/React/TS; vitest + tsc.

**Scope guardrails (do NOT do):** no recipe/preset enforcement, no hook enforcement, no HookRegistry wiring, no RuntimeProfile changes, no custom-rule wizard, no config.toml writes. Tools only. The frontend must NOT claim Verification Rules persist.

**Grounding (confirmed in the repo):**
- `magi_agent/tools/registry.py` ~lines 125-141: `enable(name)`, `disable(name)`, `resolve_registration(name)` mutate/read LIVE state.
- `magi_agent/runtime/openmagi_runtime.py` ~lines 150-158: `tool_registry = _build_core_tool_registry(...)` then `self.tool_registry = tool_registry` — the earliest stable apply point is right after that assignment in `__init__`.
- Existing write-endpoint template with auth + atomic disk write: `magi_agent/transport/app_api.py` (config/knowledge/workspace write handlers) and the auth guard `_unauthorized_response` in `magi_agent/transport/tools.py`.
- Phase 1 already provides `magi_agent/customize/store.py` (`DEFAULT_OVERRIDES`, `customize_path`, `load_overrides`) and `magi_agent/transport/customize.py` (`register_customize_routes`, `GET /v1/app/customize`).

---

## File Structure

- Modify `magi_agent/customize/store.py` — add `save_overrides(overrides, path=None)` (atomic) + `set_tool_override(name, enabled, path=None)` helper.
- Create `magi_agent/customize/apply.py` — `apply_tool_overrides(runtime, overrides)`; imports ONLY store + stdlib (no catalog/transport → no import cycle).
- Modify `magi_agent/customize/__init__.py` — re-export the new public symbols.
- Modify `magi_agent/transport/customize.py` — add `PATCH /v1/app/customize/tools/{name}`.
- Modify `magi_agent/runtime/openmagi_runtime.py` — call `apply_tool_overrides` after the tool registry is set.
- Modify `apps/web/src/components/dashboard/customize/custom-tool-modal.tsx` + `customize-tab.tsx` — async PATCH on toggle, saved+applied messaging.
- Modify `apps/web/src/lib/customize-api.ts` — add a `patchToolOverride` helper.
- Tests: extend `tests/test_customize_store.py`, new `tests/test_customize_apply.py`, extend `tests/test_customize_routes.py`, new `tests/test_customize_startup_apply.py`.
- Rebuild `magi_agent/web_dashboard/**` (Task 6).

---

## Task 1: Atomic save + tool-override helper (store.py)

**Files:** Modify `magi_agent/customize/store.py`, `magi_agent/customize/__init__.py`; Test `tests/test_customize_store.py`.

- [ ] **Step 1: Add failing tests** (append to tests/test_customize_store.py):

```python
def test_save_then_load_roundtrip(tmp_path):
    from magi_agent.customize.store import load_overrides, save_overrides
    target = tmp_path / "customize.json"
    data = load_overrides(target)  # defaults
    data["tools"]["web_fetch"] = False
    save_overrides(data, target)
    assert target.exists()
    reloaded = load_overrides(target)
    assert reloaded["tools"] == {"web_fetch": False}


def test_save_is_atomic_no_partial_temp_left(tmp_path):
    from magi_agent.customize.store import DEFAULT_OVERRIDES, save_overrides
    target = tmp_path / "customize.json"
    save_overrides(DEFAULT_OVERRIDES, target)
    # no leftover *.tmp sibling
    assert list(tmp_path.glob("*.tmp")) == []


def test_set_tool_override_creates_and_updates(tmp_path):
    from magi_agent.customize.store import load_overrides, set_tool_override
    target = tmp_path / "customize.json"
    set_tool_override("shell", False, target)
    assert load_overrides(target)["tools"]["shell"] is False
    set_tool_override("shell", True, target)
    assert load_overrides(target)["tools"]["shell"] is True


def test_save_creates_parent_dir(tmp_path):
    from magi_agent.customize.store import DEFAULT_OVERRIDES, save_overrides
    target = tmp_path / "nested" / "dir" / "customize.json"
    save_overrides(DEFAULT_OVERRIDES, target)
    assert target.exists()
```

- [ ] **Step 2:** `uv run --extra dev pytest tests/test_customize_store.py -q` → new tests FAIL (ImportError).

- [ ] **Step 3: Implement** (add to store.py):

```python
import tempfile


def save_overrides(overrides: dict[str, Any], path: Path | None = None) -> None:
    """Atomically write the overrides file (normalized). Creates parent dirs."""
    target = path or customize_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize(overrides if isinstance(overrides, dict) else {})
    payload = json.dumps(normalized, indent=2, sort_keys=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def set_tool_override(name: str, enabled: bool, path: Path | None = None) -> dict[str, Any]:
    """Load, set one tool's enabled override, save atomically, return the new overrides."""
    target = path or customize_path()
    overrides = load_overrides(target)
    overrides["tools"][name] = bool(enabled)
    save_overrides(overrides, target)
    return overrides
```

Update `__init__.py` to also export `save_overrides`, `set_tool_override`.

- [ ] **Step 4:** `uv run --extra dev pytest tests/test_customize_store.py -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add magi_agent/customize/store.py magi_agent/customize/__init__.py tests/test_customize_store.py
git commit -m "feat(customize): atomic save_overrides + set_tool_override"
```

---

## Task 2: apply_tool_overrides (apply.py)

**Files:** Create `magi_agent/customize/apply.py`; Test `tests/test_customize_apply.py`.

**Context:** `tool_registry.enable(name)`/`disable(name)` mutate live state; `resolve_registration(name)` returns None for unknown tools. Be defensive: skip names that don't resolve. READ `magi_agent/tools/registry.py` to confirm `enable`/`disable`/`resolve_registration` signatures and whether `disable` raises on unknown names (guard accordingly).

- [ ] **Step 1: Failing test** (tests/test_customize_apply.py):

```python
from magi_agent.customize.apply import apply_tool_overrides


class _Reg:
    def __init__(self, names):
        self.enabled = {n: True for n in names}

    def resolve_registration(self, name):
        return object() if name in self.enabled else None

    def enable(self, name):
        self.enabled[name] = True

    def disable(self, name):
        self.enabled[name] = False


class _RT:
    def __init__(self, names):
        self.tool_registry = _Reg(names)


def test_apply_disables_and_enables_known_tools():
    rt = _RT(["a", "b", "c"])
    apply_tool_overrides(rt, {"tools": {"a": False, "b": True}})
    assert rt.tool_registry.enabled == {"a": False, "b": True, "c": True}


def test_apply_skips_unknown_tools():
    rt = _RT(["a"])
    apply_tool_overrides(rt, {"tools": {"ghost": False}})  # must not raise
    assert rt.tool_registry.enabled == {"a": True}


def test_apply_tolerates_missing_tools_key():
    rt = _RT(["a"])
    apply_tool_overrides(rt, {})  # must not raise
    assert rt.tool_registry.enabled == {"a": True}
```

- [ ] **Step 2:** run → FAIL (module missing).

- [ ] **Step 3: Implement** magi_agent/customize/apply.py:

```python
from __future__ import annotations

from typing import Any


def apply_tool_overrides(runtime: Any, overrides: dict[str, Any]) -> None:
    """Apply persisted tool enable/disable overrides to the live tool registry.

    Defensive: unknown tools are skipped, never raises on bad input.
    """
    registry = getattr(runtime, "tool_registry", None)
    if registry is None:
        return
    tools = (overrides or {}).get("tools", {})
    if not isinstance(tools, dict):
        return
    for name, enabled in tools.items():
        try:
            if registry.resolve_registration(name) is None:
                continue
            if enabled:
                registry.enable(name)
            else:
                registry.disable(name)
        except Exception:
            continue
```

Re-export `apply_tool_overrides` from `magi_agent/customize/__init__.py`.

- [ ] **Step 4:** run → pass.

- [ ] **Step 5: Commit**

```bash
git add magi_agent/customize/apply.py magi_agent/customize/__init__.py tests/test_customize_apply.py
git commit -m "feat(customize): apply_tool_overrides to live tool registry"
```

---

## Task 3: PATCH /v1/app/customize/tools/{name}

**Files:** Modify `magi_agent/transport/customize.py`; Test `tests/test_customize_routes.py`.

**Context:** Persist via `set_tool_override`, then live-apply to `runtime.tool_registry`. Reuse `_unauthorized_response`. Mirror the test runtime construction already used in `tests/test_customize_routes.py`.

- [ ] **Step 1: Failing tests** (append to tests/test_customize_routes.py):

```python
def test_patch_tool_requires_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = _client(tmp_path)  # reuse the helper already in this file
    resp = client.patch("/v1/app/customize/tools/web_fetch", json={"enabled": False})
    assert resp.status_code == 401


def test_patch_tool_persists_and_applies(tmp_path, monkeypatch):
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    runtime = _build_runtime(tmp_path, gateway_token=_TOKEN)  # however this file builds it
    # pick a real tool name from the runtime registry
    tool_name = runtime.tool_registry.list_all()[0].name
    from fastapi.testclient import TestClient
    from magi_agent.app import create_app
    client = TestClient(create_app(runtime))
    client.headers.update({"x-gateway-token": _TOKEN})

    resp = client.patch(f"/v1/app/customize/tools/{tool_name}", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["overrides"]["tools"][tool_name] is False
    # persisted to disk
    import json
    assert json.loads(cfile.read_text())["tools"][tool_name] is False
    # applied live
    assert runtime.tool_registry.resolve_registration(tool_name).enabled is False


def test_patch_tool_bad_body(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = _client(tmp_path)
    client.headers.update({"x-gateway-token": _TOKEN})
    resp = client.patch("/v1/app/customize/tools/web_fetch", json={"nope": 1})
    assert resp.status_code == 400
```

> If this test file's existing helpers are named differently than `_client` / `_build_runtime` / `_TOKEN`, read the file and use the real ones. Keep the assertions.

- [ ] **Step 2:** run → FAIL (404).

- [ ] **Step 3: Implement** — add inside `register_customize_routes` in transport/customize.py:

```python
    @app.patch("/v1/app/customize/tools/{name}")
    async def patch_tool(name: str, request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
            return JSONResponse(status_code=400, content={"error": "enabled_bool_required"})
        enabled = body["enabled"]
        overrides = set_tool_override(name, enabled)
        apply_tool_overrides(runtime, {"tools": {name: enabled}})
        return JSONResponse(content={"overrides": overrides})
```

Add imports at top of transport/customize.py: `from magi_agent.customize.store import load_overrides, set_tool_override` and `from magi_agent.customize.apply import apply_tool_overrides`.

- [ ] **Step 4:** `uv run --extra dev pytest tests/test_customize_routes.py -q` → pass.

- [ ] **Step 5: Commit**

```bash
git add magi_agent/transport/customize.py tests/test_customize_routes.py
git commit -m "feat(customize): PATCH /v1/app/customize/tools/{name} persist+apply"
```

---

## Task 4: Startup apply in runtime

**Files:** Modify `magi_agent/runtime/openmagi_runtime.py`; Test `tests/test_customize_startup_apply.py`.

**Context:** READ `magi_agent/runtime/openmagi_runtime.py` around lines 150-160 to find the exact line after `self.tool_registry = tool_registry`. Insert the apply call there. Import locally inside `__init__` (or at module top if no cycle) — `apply.py` imports only store, so no cycle, but verify the import does not slow/booby-trap construction.

- [ ] **Step 1: Failing test** (tests/test_customize_startup_apply.py):

```python
def test_startup_applies_tool_overrides(tmp_path, monkeypatch):
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    # build a runtime once to learn a real tool name
    from tests.test_customize_routes import _build_runtime, _TOKEN  # or replicate inline
    probe = _build_runtime(tmp_path, gateway_token=_TOKEN)
    tool_name = probe.tool_registry.list_all()[0].name

    # write an override disabling that tool
    from magi_agent.customize.store import set_tool_override
    set_tool_override(tool_name, False, cfile)

    # a freshly constructed runtime must come up with that tool disabled
    fresh = _build_runtime(tmp_path, gateway_token=_TOKEN)
    assert fresh.tool_registry.resolve_registration(tool_name).enabled is False
```

> Replicate `_build_runtime`/`_TOKEN` inline if not importable.

- [ ] **Step 2:** run → FAIL (tool still enabled — apply not wired).

- [ ] **Step 3: Implement** — in `OpenMagiRuntime.__init__`, immediately after `self.tool_registry = tool_registry`:

```python
        from magi_agent.customize.apply import apply_tool_overrides
        from magi_agent.customize.store import load_overrides

        apply_tool_overrides(self, load_overrides())
```

Place it so `self.tool_registry` is already set. Keep it defensive (apply never raises). Do NOT move other init lines.

- [ ] **Step 4:** run the new test + full customize suite + a runtime smoke:
`uv run --extra dev pytest tests/test_customize_startup_apply.py tests/test_customize_store.py tests/test_customize_apply.py tests/test_customize_routes.py tests/test_app_api_routes.py -q` → all pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add magi_agent/runtime/openmagi_runtime.py tests/test_customize_startup_apply.py
git commit -m "feat(customize): apply persisted tool overrides on runtime startup"
```

---

## Task 5: Frontend — wire Custom Tools toggle to PATCH

**Files:** Modify `apps/web/src/lib/customize-api.ts`, `apps/web/src/components/dashboard/customize/custom-tool-modal.tsx`, `apps/web/src/components/dashboard/customize/customize-tab.tsx`.

**Context:** READ the three files first. Currently the Custom Tools toggle updates LOCAL state only. Phase 2: it must PATCH `/v1/app/customize/tools/{name}` and reflect the persisted result. Keep the Verification Rules modal UNCHANGED (still preview-only). Update ONLY the Custom Tools copy to say changes are saved+applied (remove its "local session only" caveat). Use the existing `useAgentFetch` for the request.

- [ ] **Step 1:** Add to `customize-api.ts`:

```typescript
export async function patchToolOverride(
  agentFetch: (path: string, init?: RequestInit) => Promise<Response>,
  name: string,
  enabled: boolean,
): Promise<CustomizeOverrides> {
  const res = await agentFetch(`/v1/app/customize/tools/${encodeURIComponent(name)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) throw new Error(`Failed to update tool (${res.status})`);
  const data = (await res.json()) as { overrides: CustomizeOverrides };
  return data.overrides;
}
```

> Match the real `useAgentFetch` return type/signature from `local-api.ts`; adjust the `agentFetch` param type if needed.

- [ ] **Step 2:** In `customize-tab.tsx`, change the Custom Tools `onToggle` handler to: call `patchToolOverride(...)`, on success set the tools-overrides local state from the returned overrides (`overrides.tools`), and surface a transient error if it rejects (reuse the error pattern already in the file). Keep per-toggle pending state so the switch can show in-flight/disabled. Verification Rules callbacks stay local-only.

- [ ] **Step 3:** In `custom-tool-modal.tsx`, update the modal subtitle/copy from "local session only" to e.g. "Changes are saved and take effect immediately." Optionally disable a toggle while its PATCH is in flight. Do not add add/delete.

- [ ] **Step 4:** `cd apps/web && npm run check` → no type errors. Run `npx vitest run` for the customize tests; update any source-string assertions that referenced the old "local session only" tool copy so they assert the new saved+applied copy.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/customize-api.ts apps/web/src/components/dashboard/customize/custom-tool-modal.tsx apps/web/src/components/dashboard/customize/customize-tab.tsx apps/web/src/lib/customize-api.local.test.ts apps/web/src/components/dashboard/customize/customize-tab.local.test.ts
git commit -m "feat(web): persist Custom Tools toggle via PATCH endpoint"
```

---

## Task 6: Rebuild dashboard bundle

**Files:** `apps/web/dist`, `magi_agent/web_dashboard/**`.

- [ ] **Step 1:** Run the canonical script from the worktree root: `bash scripts/build-web-dashboard.sh`. Confirm "Synced ... -> magi_agent/web_dashboard".
- [ ] **Step 2:** Verify the PATCH path is in the bundle: `grep -rl "v1/app/customize/tools" magi_agent/web_dashboard/_next | head` → expect ≥1 match.
- [ ] **Step 3: Commit**

```bash
git add apps/web magi_agent/web_dashboard
git commit -m "build(web): rebuild dashboard bundle with persisted tool toggles"
```

---

## Self-Review Checklist
- Scope: ONLY tools persist+apply. No recipe/preset/hook enforcement, no profile/HookRegistry changes, no config.toml writes, no custom-rule wizard. Verification Rules modal unchanged + still preview-only.
- Honesty: Custom Tools copy now truthfully says saved+applied (because it is). Verification Rules copy still says preview/local.
- No import cycle: `apply.py` imports only store; runtime imports apply (store has no runtime imports).
- Defensiveness: apply skips unknown tools and never raises; PATCH validates `enabled` is a bool.
- Regression: registering the PATCH route and the startup-apply call must not break existing tests (`test_app_api_routes.py`, `test_customize_routes.py`).

## Notes for executor
- Backend: `uv run --extra dev pytest tests/test_customize_*.py tests/test_app_api_routes.py -q`.
- Frontend: `cd apps/web && npm run check && npx vitest run`.
- If a real API differs from assumptions (tool_registry method names, runtime init line, useAgentFetch signature), STOP and report rather than inventing.
