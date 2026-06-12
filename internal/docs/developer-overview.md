# Developer Overview

Entry point for developers extending the Magi Agent runtime with custom surfaces.

Start here if you are building runtime extensions, custom harnesses, ToolHost implementations, or plugin manifests.

## Canonical runtime implementation

Magi Agent is the canonical runtime implementation. Hosted deployments still carry compatibility identifiers such as runtime='core-agent', CORE_AGENT_* env vars, and clawy-core-agent-python paths while they package the OSS Magi Agent image and selected-route rollout gates.

The runtime is built on Google ADK with an adapter layer (adk_bridge/) that maps ADK primitives to Magi Agent's evidence, boundary, and hook systems. ADK invocation is live: adk_bridge/local_runner.py constructs a real ADK Runner and drives turns through it on the CLI and hosted paths. The legacy Node.js runtime is retained as history and rollback/compatibility context only; do not use it for new runtime behavior.

## Key directories in the Python runtime

The canonical OSS package is magi_agent. In the hosted monorepo, infra/docker/clawy-core-agent-python is a compatibility packaging surface until the physical path rename lands. The directory structure with file counts:

- runtime/ : openmagi_runtime.py (core container), adk_turn_runner.py (live ADK turn execution), error_taxonomy.py (ErrorCategory + DecisionAction), session_identity.py, session_continuity.py, commit_boundary.py, child_runner_boundary.py, child_runner_live.py, message_builder.py, loop_detectors.py, phase_routing.py, and the broader turn/context/streaming families.
- adk_bridge/ : primitives.py (ADK primitive detection), runner_adapter.py, tool_adapter.py, callback_adapter.py (ADK callbacks to HookPoint mapping), local_runner.py (constructs the live ADK Runner), local_toolhost.py, session_service.py, memory_service.py, artifact_service.py, control_plane.py. Status: live — local_runner.py wires a real ADK Runner that the engine drives each turn.
- evidence/ (15 files): ledger.py (EvidenceLedgerEntry with sequence, producer_surface, secret redaction), types.py (EvidenceRecord, EvidenceSource, EvidenceRequirement, EvidenceContract, EvidenceContractVerdict), tool_boundary.py, extractors.py, reports.py, rollout.py, source_ledger.py. Evidence contract verdicts are enforced live by the engine pre-final gate / verifier bus. 15 BUILTIN_EVIDENCE_TYPES.
- harness/ (18 files): engine.py (HarnessEngine.resolve()), resolved.py (build_default_resolved_harness_state), profiles.py (RuntimeProfile with HardSafetyPolicy 5 gates + 5 FeaturePacks), evidence_scope.py (EvidenceContractScope).
- hooks/ (7 files): registry.py (HookRegistry with HookRegistration), bus.py (HookBus.run() with filter/execute/catch flow), manifest.py (15 HookPoint values).
- tools/ : catalog.py (declares the core tool manifests — file read/write/edit, patch apply, Glob, Grep, Bash, etc.), registry.py (ToolRegistry), manifest.py (ToolManifest with permission, budget, dangerous, parallel_safety, side_effect_class), dispatcher.py. Tool dispatch is live: the CLI binds real handlers (file/search/execute plus MemoryWrite and InspectSelfEvidence) and exposes only manifests that have a bound handler. Manifests without a bound handler are declared-but-not-yet-exposed (see [Tools](/docs/tools)).
- memory/ (9 files): contracts.py (MemoryRecord, RecallRequest, RecallResult with write_allowed=Literal[False]), write_boundary.py, adapters/hipocampus_readonly.py.
- plugins/ (7 files): manager.py (PluginStatus with traffic_attached=False, execution_attached=False), native_catalog.py.
- recipes/ (compiler.py): RecipePackManifest, PackRegistry, ProfileResolver (5-layer merge), AgentRecipeCompiler, RecipeSnapshot.
- shadow/ (52 files): gate1 through gate5b diagnostic testing infrastructure.
- transport/ (9 files): chat.py (POST /v1/chat/completions), health.py (/health, /healthz), shadow routes.

## Running tests

The Magi Agent runtime uses pytest for runtime, recipe, and fixture suites. In the hosted packaging mirror, run focused Python tests from the infra/docker/clawy-core-agent-python directory; in OSS development, run tests from the openmagi/magi-agent checkout.

The web frontend and chat-proxy use Vitest. Run npx vitest run from the project root for frontend tests, or from the specific infrastructure directory for service tests. Shadow gate tests validate diagnostic replay (gate3a), real-time simulation (gate3b), dry-run comparison (gate4), and canary routing (gate5a/5b).

## Extension points

The runtime exposes five primary extension points for developers. Each is documented in detail on its own page. Additional extension surfaces are planned as the plugin system matures.

- Hooks: Register HookManifest instances at any of the 15 HookPoint lifecycle points. See [Hook Points](/docs/hook-points).
- Evidence contracts: Define EvidenceContract instances that gate agent output based on accumulated evidence. See [Evidence Contracts](/docs/evidence-contracts).
- Harness presets: Create BuiltinHarnessPreset configurations that bundle hooks, evidence contracts, and harness rules. See [Harnesses](/docs/harnesses).
- Recipe packs: Package task-specific configurations as RecipePackManifest with tool refs, callback refs, and evidence refs. See [Recipes](/docs/recipes).
- Plugins: Bundle tools, hooks, and harness rules into a PluginManifest with declared permissions and sandbox policy. See [Plugin Manifest](/docs/plugin-manifest). An external plugin registry is planned but not yet available.

## What not to modify

Boundary authority flags use Literal[False] types to make them structurally unmodifiable at the type level. Do not override these flags in custom code. The fields traffic_attached and execution_attached in EvidenceRolloutMetadata are typed as Literal[False], meaning they can only hold the value False. Changing them to True requires a code change in the boundary module itself, not a configuration change.

Enforcement defaults (the default-off posture of all boundary modules) should not be changed without going through the staged rollout process described in [Default-Off Gates](/docs/default-off-gates), which defines Stage 1/2/3 and the promotion criteria. Disabling hard_safety on evidence contracts or harness presets that are marked security_critical is strongly discouraged.
