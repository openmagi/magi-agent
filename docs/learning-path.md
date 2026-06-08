# Learning Path

Guided learning paths for users, recipe authors, runtime extenders, and self-hosters.

Choose your path through the Magi Agent documentation based on your role and goals.

## I want to use Magi Agent

Start with the install and first-run instructions, then learn how tools, memory, and security interact during a governed agent run.

- Install and first run: See [Getting Started](/docs/getting-started) for the Homebrew install and provider-key setup.
- First task walkthrough: See [Quickstart](/docs/quickstart) for the Homebrew + provider-key + `magi -p` happy path.
- What works today: See [What Works Today](/docs/what-works-today) for the local capabilities you can run now.
- Configuration: Set one provider env key or create `~/.magi/config.toml`; see [Getting Started](/docs/getting-started) for details.
- Tools and evidence: See [Tools](/docs/tools) for how tool calls produce ToolEvidenceRecord entries in the evidence ledger.
- Memory and continuity: See [Memory](/docs/memory) for session memory, compaction, and workspace state.
- CLI reference: See [CLI](/docs/cli/magi) for `magi` flags, output modes, and permission modes.
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
- ToolHost: See [ToolHost](/docs/toolhost) for ToolManifest schema, ToolRegistry, first-party tool catalog, and governed dispatch.

## I want to self-host

Magi Agent installs via Homebrew today; contributors can also run from a source checkout with uv. Self-hosting starts with install and configuration.

- Install: See [Getting Started](/docs/getting-started) for the Homebrew install and the contributor-only `uv sync` / `uv run --extra cli magi` source path.
- Deployment options: See [Deployment](/docs/deployment) for local-first operation and optional managed hosting.
- Configuration: Set one provider env key or create `~/.magi/config.toml`; see [Getting Started](/docs/getting-started) for details.
- Security hardening: See [Security](/docs/security) for the default-off model and how to enable boundaries for production.
- Authority posture: See [Security](/docs/security) and [Boundaries](/docs/boundaries) for least-privilege tools, approvals, and default-off boundary behavior.
