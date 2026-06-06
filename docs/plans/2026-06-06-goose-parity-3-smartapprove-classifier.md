# PR3 — SmartApprove read-only classifier (optional augmentation to the rules gate)

**Lesson source:** goose has a `SmartApprove` permission mode
(`crates/goose/src/permission/permission_inspector.rs` `inspect`/`detect_read_only_tools`)
that, on a permission miss, asks an LLM to classify an unknown tool as read-only and caches
the verdict — recovering safe read-only calls without prompting. magi's gate
(`magi_agent/cli/permissions.py`) is purely rules-based and **fails safe (denies/asks on any
rule miss)**, which is correct but high-friction for unknown tools.

**Goal:** add an *optional* `smartApprove` mode that, **only when a rule miss would produce
`ask`**, consults a manifest-first → cache → LLM classifier to recover read-only calls.
Never overrides an explicit `deny`. Fails CLOSED. Every LLM verdict is evidence-logged and
cached for reproducibility. Default OFF.

## Current state (verified, on `origin/main` @ debd41d)

- `magi_agent/cli/permissions.py`:
  - `RulesEngine.evaluate(req: ControlRequest) -> RuleVerdict` — most-specific rule wins,
    deny > allow > ask; **rule miss returns `"ask"`** (no silent allow).
  - `RulesPermissionGate.check(req) -> PermissionDecision` — `allow`/`deny` short-circuit;
    `"ask"` runs `_race(req)` over sinks (fail-closed `deny` if no sink / all error).
  - Modes: `default` / `acceptEdits` / `bypassPermissions`.
- `magi_agent/cli/engine.py` — gate runs as a prepended `before_tool_callback`
  (`_gate_before_tool` builds `ControlRequest(toolName, arguments, reason="tool_use")`,
  `_attach_gate_callback` ~722).
- **Tool read-only metadata already exists** (`magi_agent/tools/manifest.py`):
  `dangerous: bool`, `mutates_workspace: bool`,
  `side_effect_class: SideEffectClass` (default `"none"`),
  `parallel_safety: ParallelSafety` (default `"unsafe"`), with validators guaranteeing
  `parallel_safety ∈ {readonly,concurrency_safe} ⟹ not dangerous and not mutating`.
  `ToolRegistry.resolve(name) -> ToolManifest | None`. **So known tools need no LLM.**
- goose classifier reference: `permission_judge.rs:22-49` rubric (SELECT/read → read-only;
  INSERT/UPDATE/DELETE/write/send → not; "if unsure, not read-only"); uses default
  provider; fails to `vec![]` (→ effectively closed); caches by tool name in
  `permission.yaml`.

## Design

### A. `magi_agent/cli/readonly_classifier.py` (new)
```python
class ReadOnlyClassifier:
    def __init__(self, *, registry: ToolRegistry | None = None,
                 model_factory: Callable[[], object] | None = None,   # returns a LiteLlm
                 evidence_sink: Callable[[dict], None] | None = None) -> None: ...
    def manifest_verdict(self, tool_name: str) -> bool | None:
        # free, deterministic; None if tool unknown to registry
        m = self._registry.resolve(tool_name) if self._registry else None
        if m is None: return None
        if m.dangerous or m.mutates_workspace or m.side_effect_class != "none":
            return False
        return m.parallel_safety in ("readonly", "concurrency_safe")
    async def classify(self, req: ControlRequest) -> bool:
        # 1) manifest_verdict (deterministic, preferred)
        # 2) per-session name-keyed cache
        # 3) LLM classify -> FAIL CLOSED (return False) on ANY error
        #    on success: cache + emit evidence; on error: emit evidence(error) + return False
```
- LLM step reuses magi's existing LiteLlm path (mirror `real_runner._build_litellm_model`)
  with a fast-model override env `MAGI_SMART_APPROVE_MODEL` (default = main model if unset).
  Prompt = goose rubric, but feed `name + description + input_schema` (richer than goose).
  Require a strict JSON reply `{"read_only": bool, "reason": str}`.
- Cache: in-memory `dict[str, bool]` keyed by tool name (per session). No disk persistence
  in v1 (keeps runs replayable from the evidence log, not hidden disk state).

### B. Gate integration — `permissions.py`
```python
class RulesPermissionGate(PermissionGate):
    def __init__(self, *, rules=None, sinks=None, store=None,
                 smart_approve: ReadOnlyClassifier | None = None) -> None:
        ...
        self._smart_approve = smart_approve   # None == disabled (DEFAULT)

    async def check(self, req: ControlRequest) -> PermissionDecision:
        verdict = self.rules.evaluate(req)
        if verdict == "allow": return PermissionDecision(kind="allow")
        if verdict == "deny":  return PermissionDecision(kind="deny")   # NEVER auto-recovered
        # verdict == "ask" (rule miss) — try SmartApprove BEFORE the sink race
        if self._smart_approve is not None and await self._smart_approve.classify(req):
            return PermissionDecision(kind="allow")
        decision = await self._race(req)         # unchanged
        if decision.updates: self.rules.add_rules(decision.updates)
        return decision
```
Critical invariants:
- Classifier is consulted **after** the `deny` early-return, so it can only turn `ask` →
  `allow`, never touch an explicit deny rule.
- `acceptEdits` / `bypassPermissions` modes unchanged.

### C. Wiring — `engine.py`
Construct `ReadOnlyClassifier` from the in-scope `ProviderConfig` + `ToolRegistry` and pass
`smart_approve=...` to the gate **only** when a new mode `"smartApprove"` is selected
(parallel to goose's `SmartApprove`). Default selection leaves `smart_approve=None` (OFF).
The `_gate_before_tool` seam needs no change.

### D. Determinism / evidence
- manifest-first means the vast majority of decisions are deterministic and need no LLM.
- Every LLM verdict (and manifest/cache/error path) emits an evidence record
  `smart_approve_classification` via `evidence_sink`: `{tool, verdict, reason, source ∈
  {manifest,cache,llm,classifier_error}, model}`. Register the type in the evidence
  registry. For replay, the per-session cache can be seeded from a prior run's evidence log
  so a re-run short-circuits at the cache and reproduces the verdict without re-invoking the
  model.

### E. Failure behavior — FAIL CLOSED
`classify()` returns `False` on any exception / missing model → falls through to the normal
`ask` race → safe `deny` if no sink. Stricter than goose because `manifest_verdict` also
treats `side_effect_class != "none"` as not-read-only.

## Tests (TDD — write first)
- `tests/cli/test_readonly_classifier.py` (new): `manifest_verdict` true for readonly tools,
  false for dangerous/mutating, None for unknown; cache short-circuits a 2nd call; LLM error
  → `False` + error evidence; success → cache + evidence.
- `tests/cli/test_permissions_smartapprove.py` (new): explicit `deny` rule is NEVER
  overridden by the classifier; rule-miss `ask` + classifier `True` → `allow`; classifier
  `False` → falls to `_race`; `smart_approve=None` → identical to today.
- Evidence type registration test.

## Acceptance criteria
1. New `ReadOnlyClassifier` (manifest → cache → LLM, fail-closed, evidence-logged).
2. `RulesPermissionGate` augmented; explicit deny never recovered; default `smart_approve=None`.
3. New `smartApprove` mode wires the classifier in `engine.py`; default mode = OFF, behavior
   byte-identical to today.
4. LLM verdicts emit `smart_approve_classification` evidence; cache enables replay.
5. `uv run --extra dev pytest -q` green for touched modules.

## Out of scope
- Disk-persisted permission cache. Auto-allowing non-read-only tools. Changing the default
  permission mode.
