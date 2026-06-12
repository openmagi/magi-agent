# Typed-Context API Reference (the capability ceiling)

Every primitive impl receives ONLY a narrow, typed context exposing exactly its
type's capabilities. **First-party impls receive the same objects** — there is
no richer first-party handle (capability parity). Source:
`magi_agent/packs/context.py`. Contexts carry a frozen `Capability` token set
that full-trust local does not gate; a hosted build can later restrict it
without changing any impl signature.

## Registration-time provide contexts (one per provides type)

Your manifest's `impl = "module:symbol"` resolves to a callable invoked once at
load time with the matching provide context:

| `provides` type | Context class | What you call |
|---|---|---|
| `tool` | `ToolProvideContext` | `register(tool_manifest)` — a `magi_agent.tools.manifest.ToolManifest`. |
| `callback` | `CallbackProvideContext` | `register(hook_manifest, handler)` — a `magi_agent.hooks.manifest.HookManifest` plus a `HookContext -> HookResult` handler. |
| `evidence_producer` | `EvidenceProducerProvideContext` | `register(ref, spec)` — a `ProducerSpec(evidence_type, public_ref, producer_surfaces)`; `public_ref` must carry a recognized public-ref prefix (`evidence:` / `verifier:` / `receipt:sha256:` / `sha256:`). |
| `recipe` | `RecipeProvideContext` | `register(ref, manifest)` — disk packs normally use a declarative `spec` file instead (no code). |
| `connector` | `ConnectorProvideContext` | `register(ref, spec)` — a `ConnectorSpec(server_ref, tool_manifests, readonly)`. |
| `harness` | `HarnessProvideContext` | `register(ref, pack)` — a `magi_agent.harness.resolved.ResolvedHarnessPack`. |
| `control_plane` | `ControlPlaneProvideContext` | `register(loop_control)` per control you build. Also carries read-only collaborators: `env` (mapping for your own gating), `general_automation_receipts`, `contract_required`, `agent_role`, `self_review_fork_runner`, `self_review_candidate_sink`, `self_review_config`, `self_review_now`, `self_review_scheduler`, `tool_synthesis_model_label`. First-party's bundled controls receive the IDENTICAL object. |
| `validator` | — | validators register declaratively; the impl itself is the invoke-time callable below (one positional `ValidatorCtx` parameter). |

## Invoke-time contexts

- `ValidatorCtx` — what a `validator` impl receives per evaluation: `ref`, a
  read-only `artifact` mapping, `session` (a `SessionReadView`). Emit with
  `ctx.emit(passed=..., detail=...)` and return `ctx.verdict()` (a
  `ValidatorVerdict(ref, passed, detail)`).
- Control-plane hook contexts (built by the dispatcher per fan-out):
  - `BeforeToolCtx` — `tool_name`, read-only `tool_args`, `session`,
    `evidence`; decide via `ctx.decide("allow" | "deny" | "rewrite", ...)`.
    A deciding before_tool impl must declare `gatePosition = "before"` in its
    manifest or the dispatcher raises `GatePositionViolation`.
  - `AfterToolCtx` — `tool_name`, `tool_args`, `result`; `ctx.override(result)`
    (first non-None override wins).
  - `BeforeModelCtx` — `ctx.reinject(role=..., text=...)` and
    `ctx.clear_tools()` mutate the outgoing model request.
  - `AfterAgentCtx` — observe-only completed turn.
- `ControlPlaneContext` — the shared seam carrier for `control_plane` impls
  (first-party and user receive the identical object): `evidence`
  (`EvidenceLedgerView`), `turn_snapshot` (`TurnSnapshot`), `fork_runner`
  (public ForkRunner capability — full-trust local), `per_invocation`
  (`PerInvocationState`, the only mutable struct: LRU-bounded, cleared on turn
  complete), `compaction` (narrowed compaction-decision capability).
- Also defined by the ABI but not yet built by a live dispatch path: `ToolCtx`
  (tool invoke-time read view plus a `progress()` sink) and
  `EvidenceProducerCtx` (an `emit(evidence_type=..., payload=...)` collector).
  They pin the invoke-time shape those types will receive; tools registered via
  `ToolProvideContext` execute through the regular tool host today.

## Read views

- `SessionReadView` — frozen projection: `invocation_id`, `agent_name`,
  `turn_index`, `get_state(key, default)`, `state_keys()`. Never aliases live
  ADK state.
- `EvidenceReadView` — `present`, `owed`, `has(evidence_type)`.

## `Capability` tokens

`read_session`, `read_evidence`, `decide_tool`, `rewrite_tool_args`,
`override_tool_result`, `mutate_model_request`, `reinject_message`,
`clear_tools`, `emit_validation`, `emit_evidence`, `spawn_agent`. Local
full-trust passes the full set; the tokens reserve the hosted-restriction seam.
