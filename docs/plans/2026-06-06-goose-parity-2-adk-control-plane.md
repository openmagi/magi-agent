# PR2 — ADK loop control-plane abstraction

**Lesson source:** goose *owns* its agent loop (`crates/goose/src/agents/agent.rs:1706`
`loop {}`), so turn-cap, stop-hooks, retry, and "disable tools on the final iteration" are
inline and immediate. magi delegates the loop to Google ADK's `Runner.run_async`, so every
such control must be hand-wired as a bespoke ADK callback/plugin in multiple build sites —
and some are still unwired "seams." Verified concrete pain: **`real_runner.py` builds
`App(..., plugins=[])` (empty)** while `local_runner.py` assembles edit-retry / resilience /
compaction plugins — so those controls never reach the production CLI runner at all.

**Goal:** a thin, typed control-plane over ADK's callback surface so adding a loop control
is a one-line registration applied **once** at runner-build time, across **both** runners —
closing the `plugins=[]` divergence and turning unwired seams into one-liners.

## Current state (verified, on `origin/main` @ debd41d)

- `magi_agent/cli/real_runner.py` — production CLI runner. Builds
  `App(name=..., root_agent=agent, plugins=[])`. **Gets none of the plugins below.**
- `magi_agent/adk_bridge/local_runner.py` — assembles `runner_plugins` by manual append
  (`edit_retry_plugin`, `resilience_plugin`, `compaction_plugin`, each behind a default-off
  env parse) and passes them to `App(plugins=runner_plugins)`.
- `magi_agent/cli/engine.py` — `_attach_gate_callback` (~722) imperatively **prepends** the
  permission gate to `agent.before_tool_callback` per-turn (WIRED), with a `_GateAttachment`
  restore handle used in `_drive`'s `finally`. This gate is per-turn (carries
  `cancel`/`turn_id`) and must keep running first.
- Plugins implement ADK callbacks: `ResilienceShimPlugin.after_tool_callback`,
  `EditRetryReflectionPlugin.after_tool_callback`/`on_tool_error_callback`,
  `ContextCompactionPlugin.before_model_callback`.
- `magi_agent/adk_bridge/callback_adapter.py:build_adk_callback_adapter` exists but is a
  one-way HookBus projection that fails closed — **not** a control registry. Do not reuse
  it as the plane.
- Unwired seam: `magi_agent/runtime/turn_policy.py` `maybe_apply_max_steps_brake` (~63),
  docstring says "Intentionally-unwired seam: the runner must call this per-iteration."

## Design

### A. `magi_agent/adk_bridge/control_plane.py` (new)
```python
@dataclass(frozen=True)
class ToolDecision:
    action: Literal["allow", "deny", "rewrite"] = "allow"
    deny_result: dict[str, Any] | None = None    # ADK skip-result on deny
    updated_args: dict[str, Any] | None = None   # in-place rewrite on allow

@runtime_checkable
class LoopControl(Protocol):
    name: str
    async def on_before_tool(self, *, tool, args, tool_context) -> ToolDecision | None: ...
    async def on_after_tool(self, *, tool, args, tool_context, result) -> dict | None: ...
    async def on_before_model(self, *, callback_context, llm_request) -> None: ...

class ControlPlane:
    def __init__(self) -> None: self._controls: list[LoopControl] = []
    def register(self, control: LoopControl) -> "ControlPlane": ...   # returns self (chainable)
    async def _before_tool(self, *, tool, args, tool_context) -> dict | None:
        # ordered fan-out; first deny wins; rewrite mutates args in place
    async def _after_tool(self, *, tool, args, tool_context, result) -> dict | None:
        # ordered; first non-None override wins
    async def _before_model(self, *, callback_context, llm_request) -> None:
        # all controls run (mutation accumulates)
```

Default `LoopControl` methods should be optional (a control can implement only the hooks it
needs) — provide a `BaseLoopControl` with no-op defaults so each control overrides one hook.

### B. Single fan-out plugin
```python
class ControlPlanePlugin(BasePlugin):
    def __init__(self, plane: ControlPlane) -> None:
        super().__init__("magi_control_plane"); self._p = plane
    async def before_tool_callback(self, *, tool, tool_args, tool_context):
        return await self._p._before_tool(tool=tool, args=tool_args, tool_context=tool_context)
    async def after_tool_callback(self, *, tool, tool_args, tool_context, result):
        return await self._p._after_tool(...)
    async def before_model_callback(self, *, callback_context, llm_request):
        return await self._p._before_model(...)
```
Verify the exact ADK 1.33 `BasePlugin` callback signatures in the installed package before
finalizing argument names; match them precisely.

### C. Wiring (both runners — closes the gap)
- `local_runner.py`: replace the manual append block with
  ```python
  plane = ControlPlane()
  if edit_retry_plugin_control: plane.register(edit_retry_plugin_control)
  if resilience_control:        plane.register(resilience_control)
  if compaction_control:        plane.register(compaction_control)
  app = App(..., plugins=[ControlPlanePlugin(plane)])
  ```
  Migrate the 3 existing plugins to `LoopControl` adapters (thin wrappers delegating to the
  current plugin logic) OR keep them as plugins and also add the plane — but the point is a
  single composition path. Prefer adapters so there is ONE mechanism.
- `real_runner.py`: build the same `plane` from the same env parses and pass
  `plugins=[ControlPlanePlugin(plane)]` — **this is the fix for the empty `plugins=[]`**.
  Extract a shared helper (e.g. `adk_bridge/control_plane.build_default_plane(env)`) used by
  both runners so they cannot drift again.

### D. Permission gate composition (must not break)
Leave `engine.py:_attach_gate_callback` exactly as-is. ADK runs **agent-level**
`before_tool_callback` before **plugin-level** `before_tool_callback`, so the prepended gate
still fires first and a deny short-circuits before the plane. Document this ordering in the
control_plane module docstring. (Optional later phase: wrap the gate as a per-turn
`LoopControl`; out of scope here.)

### E. Make the unwired seam a one-liner (proof of value, DEFAULT-OFF)
Add `MaxStepsBrake(BaseLoopControl)` whose `on_before_model` calls
`maybe_apply_max_steps_brake` against `llm_request.contents` and, when the brake fires, also
clears `llm_request.config.tools` (ADK-native "disable tools on final iteration"). Register
it only when a new env flag `MAGI_MAX_STEPS_BRAKE_ENABLED` (default `0`/off) is set. This
demonstrates the plane and wires the seam without changing default behavior.

## ADK honesty (document as known limitations, do NOT try to force)
These cannot be expressed via ADK callbacks and remain `engine.py` outer-driver concerns:
hard turn-cap counting, stop-hook-deny → force re-iteration, stop-on-goal re-entry after
`end_turn`. The plane covers before/after-tool (deny/rewrite) and before-model (mutation,
incl. tool-disable) only.

## Tests (TDD — write first)
- `tests/adk_bridge/test_control_plane.py` (new): registration is chainable; `_before_tool`
  fan-out — first deny wins, rewrite mutates args, allow passes through; `_after_tool` first
  override wins; `_before_model` runs all controls; a `LoopControl` implementing only one
  hook works via `BaseLoopControl` defaults.
- `tests/adk_bridge/test_control_plane_plugin.py` (new): `ControlPlanePlugin` forwards each
  ADK callback to the plane (use fakes; assert signatures match installed ADK).
- `tests/.../test_runner_plugin_parity.py` (new): `real_runner` and `local_runner` build a
  plane with the *same* controls from the same env (no `plugins=[]` divergence).
- `tests/.../test_max_steps_brake_control.py` (new): with flag on, `on_before_model` injects
  the wrap-up message and clears tools on the final iteration; flag off → no-op.

## Acceptance criteria
1. New `control_plane.py` with `LoopControl`/`BaseLoopControl`/`ControlPlane`/`ControlPlanePlugin`.
2. Both runners build the plane via one shared helper; `real_runner` no longer passes `plugins=[]`.
3. Existing 3 controls run under the plane on BOTH runners; permission gate still fires first.
4. `MaxStepsBrake` registers in one line behind `MAGI_MAX_STEPS_BRAKE_ENABLED` (default off);
   default behavior unchanged.
5. `uv run --extra dev pytest -q` green for touched modules.

## Out of scope
- Wrapping the permission gate as a LoopControl. Implementing turn-cap/stop-on-goal in the
  plane (those stay in engine.py / PR4). Flipping any flag default to on.
