# Learning Path

Guided learning paths for users, recipe authors, runtime extenders, and self-hosters.

Choose your path through the Magi Agent documentation based on your role and goals.

## I want to use Magi Agent

Start with the install and first-run instructions, then learn how tools, memory, and security interact during a governed agent run.

- Install and first run: See [Getting Started](/docs/getting-started) for source checkout and the planned Homebrew installer.
- First task walkthrough: See [Quickstart](/docs/quickstart) for a step-by-step example with evidence and boundary decisions.
- Configuration: See [Configuration](/docs/configuration) for magi-agent.yaml fields and how runtime enforcement is configured.
- Tools and evidence: See [Tools](/docs/tools) for how tool calls produce ToolEvidenceRecord entries in the evidence ledger.
- Memory and continuity: See [Memory](/docs/memory) for session memory, compaction, and workspace state.
- Security model: See [Security](/docs/security) for default-off boundaries, authority flags, and the trust model.

## I want to build recipes

Recipes are composable task profiles that declare what evidence, hooks, and harness rules apply to a class of agent work. Start with the recipe concepts, then build one.

- Recipe system overview: See [Recipes](/docs/recipes) for RecipePackManifest, first-party packs, and the recipe compiler.
- Build your own recipe: See [Build a Recipe](/docs/build-a-recipe) for the step-by-step authoring workflow.
- Evidence contracts: See [Evidence](/docs/evidence) for EvidenceRecord, EvidenceContract, and the deterministic contract engine.
- Evidence contract authoring: See [Evidence Contracts](/docs/evidence-contracts) for writing and testing contract requirements.
- Testing locally: See [Testing Recipes](/docs/testing-recipes) for contract tests with fixture evidence and pytest.

## I want to extend the runtime

The runtime exposes typed hook points (hooks/manifest.py, 15 HookPoint values), HookRegistry + HookBus dispatch (hooks/registry.py, hooks/bus.py), boundary interfaces, and a ToolHost surface (tools/catalog.py, tools/registry.py, tools/manifest.py) for extending agent behavior without forking the core.

- Runtime architecture: See [Runtime](/docs/runtime) for the FastAPI entry point, RunnerSessionBoundary turn loop, and the two-plane model-visible loop vs runtime-only control plane.
- System architecture: See [Architecture](/docs/architecture) for the MODEL-VISIBLE LOOP and RUNTIME-ONLY CONTROL PLANE separation.
- Boundary modules: See [Boundaries](/docs/boundaries) for the seven implemented boundary modules and the Intent-to-Receipt pattern.
- Hook points: See [Hook Points](/docs/hook-points) for the 15 HookPoint enum values, HookRegistry registration, and HookBus dispatch.
- Runtime interfaces: See [Runtime Interfaces](/docs/runtime-interfaces) for TurnInput, HookBusRunResult, EvidenceLedgerEntry, ToolRegistration, RecipeSnapshot, and all typed Python interfaces.
- ToolHost: See [ToolHost](/docs/toolhost) for ToolManifest schema, ToolRegistry, tool catalog (all default-off), and dispatch (currently BLOCKED).

## I want to self-host

Magi Agent runs locally from source today. A packaged local app installer is planned but not yet shipped. Self-hosting starts with the source checkout and configuration.

- Source install: See [Getting Started](/docs/getting-started) for git clone, npm install, and the npm run magi commands.
- Deployment options: See [Deployment](/docs/deployment) for local-first operation and optional managed hosting.
- Configuration: See [Configuration](/docs/configuration) for magi-agent.yaml, environment variables, and runtime enforcement.
- Security hardening: See [Security](/docs/security) for the default-off model and how to enable boundaries for production.
- Activation gates: See [Default-Off Gates](/docs/default-off-gates) for the staged rollout pattern from disabled to production authority.
