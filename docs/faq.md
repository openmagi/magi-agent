# Frequently Asked Questions

Answers to common questions about Magi Agent: recipes vs harnesses, default-off boundaries, local testing, repair decisions, model compatibility, and custom evidence types.

Answers to the most common questions about Magi Agent architecture, recipe and harness differences, testing, and extensibility.

## Can Magi actually run tasks locally today?

Yes. With a provider key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` / `GOOGLE_API_KEY`, or `FIREWORKS_API_KEY`) or a `~/.magi/config.toml`, the local `magi` CLI runs a real model (via LiteLlm) and first-party local tools — file read/write/edit, patch apply, and Bash — behind permission prompts (`default` / `acceptEdits` / `bypassPermissions`). With no key it still launches against a model-free stub.

What's default-off is the **enforcement/governance layer** (the boundary modules that can block or gate behavior) plus **external delivery and integrations** (Telegram/Discord live send, Composio) — not the agent's ability to do work. See [What works today](/docs/what-works-today).

- [What works today](/docs/what-works-today)
- [Boundaries](/docs/boundaries)

## What models does Magi Agent support?

Magi Agent is model-agnostic. Configure any supported provider and model via CORE_AGENT_MODEL in your environment. The runtime's evidence and boundary system works regardless of which model generates proposals.

## Does it cost money?

Magi Agent is open source and free to run. You pay only for the model API calls to your chosen provider (Anthropic, OpenAI, Google, etc.). Open Magi Cloud is optional managed hosting for teams that do not want to self-host.

## How is this different from ChatGPT or Claude?

ChatGPT and Claude are chat interfaces. Magi Agent is a runtime that wraps any model with deterministic evidence, boundary checks, and governed output. The model proposes actions; the runtime verifies them before they become results. You get the model's intelligence plus structural guarantees that prompt-only tools cannot provide.

## What is the difference between a recipe and a harness?

A recipe is a metadata-only policy compilation unit. RecipePackManifest declares what a recipe references (instructions, tools, callbacks, validators, evidence, approvals) and how packs compose. The recipe compiler merges profile layers and selects packs but does not execute anything.

A harness is the evidence contract resolution and enforcement engine. HarnessEngine takes evidence contracts and hooks, resolves them against agent role, spawn depth, and run context, and produces ResolvedHarnessPresetState. The harness determines which evidence contracts are active, which hooks fire, and what enforcement level applies.

In short: recipes compile policy, harnesses enforce it.

- [Recipes overview](/docs/recipes)
- [Harnesses overview](/docs/harnesses)

## Why are boundaries default-off?

Structural safety via Literal[False] authority flags. PythonRuntimeAuthorityConfig has eight fields typed as Literal[False]: transcript_write_allowed, sse_write_allowed, channel_write_allowed, db_write_allowed, workspace_mutation_allowed, child_execution_allowed, mission_runtime_allowed, and evidence_block_mode_allowed. Pydantic validates that these fields can only hold the value False. No configuration input, environment variable, or model proposal can set them to True.

This means the Python runtime structurally cannot perform write operations, deliver to channels, execute child agents, or block on evidence. The boundary is not a policy decision that can be overridden; it is a type-level invariant enforced by the _FalseOnlyModel base class.

- [Security](/docs/security)
- [Config reference](/docs/config-reference)

## How do I test a recipe locally?

Use fixture evidence with the evidence contract engine directly. Build EvidenceRecords for the contract's requirements and call evaluate_evidence_contract (evidence/contracts.py) to produce an EvidenceContractVerdict without requiring live tool execution. This lets you exercise a recipe's evidence contract against local fixture evidence records.

The verdict reports states like "satisfied", "audit", or "block_ready". A "block_ready" verdict means evidence blocking would fire in production. On the live path the engine pre-final gate consumes that verdict (coding-domain turns block by default); the research final-projection gate records the same verdict for diagnostics only ("block_ready_local_fake") because its final_answer_blocking_enabled flag is Literal[False].

- [Evidence contracts](/docs/evidence-contracts)

## Why is there no RepairDecision type?

Repair is implicit in the enforcement path. When a coding-domain evidence contract is unsatisfied at the engine pre-final gate and MAGI_CODING_REPAIR_LOOP_ENABLED is set, the gate drives a repair loop rather than exposing a standalone RepairDecision return type. RepairDecision and RepairPlan exist in harness/repair_policy.py for single-contract repair steps (max 5 attempts per plan). Cross-boundary repair orchestration across multiple contracts is not yet integrated.

The repair flow is: evidence missing or failed -> engine pre-final gate reaches block_ready -> repair loop retries the operation -> gate re-evaluates with new evidence.

- [Repair and fallback](/docs/repair-fallback)

## Can I use recipes with any LLM provider?

Yes. Recipes are model-agnostic metadata. RecipePackManifest contains no provider-specific fields. The recipe compiler operates on profile layers (user_profile, workspace_policy, task_profile, recipe_pack_config, runtime_context) that are provider-independent. The model field in RuntimeConfig is a plain string; the recipe system does not inspect or branch on it.

The ADK bridge layer handles provider-specific callback mapping (e.g. before_model_callback -> BEFORE_LLM_CALL), but this is transparent to the recipe and harness layers.

## Why doesn't recipe execution work yet?

RecipePackManifest is metadata-only today. The live_tool_refs, live_callback_refs, and runner_route_refs fields are validated empty and serialize to empty tuples. The validator explicitly rejects non-empty values with "recipe pack manifests must remain metadata-only".

The execution engine is the planned boundary that will wire manifest refs (instruction_refs, tool_refs, callback_refs, validator_refs, evidence_refs) to runtime primitives. Until that boundary is implemented, recipes declare policy intent without executing it.

This is specific to the recipe-pack compilation layer — it does not mean the agent can't act. The local CLI already runs a real model and first-party tools today (see [What works today](/docs/what-works-today)); what's pending is the metadata-driven recipe execution engine.

- [Recipe schema](/docs/recipe-schema)

## How do I add a custom evidence type?

Use the custom:PascalCaseName naming convention. Create an EvidenceRecord with type set to a string like "custom:DeploymentVerification" or "custom:SecurityScan.Result". The name must be at most 80 characters and match ^custom:[A-Z][A-Za-z0-9]*(?:[._-][A-Za-z0-9]+)*$.

Custom evidence types work with the same EvidenceRequirement and EvidenceFieldMatcher system as built-in types. You can create evidence contracts that require custom evidence types and use field matchers (equals, oneOf, matches, exists) to validate their fields.

- [Evidence types reference](/docs/evidence-types-reference)

## What happens when evidence is missing?

The behavior depends on the on_missing field of the EvidenceContract. When set to "audit", missing evidence is logged to the audit ledger but does not block the run. When set to "block_final_answer", missing evidence prevents the final answer from being projected to the user.

On the live path, coding-domain turns enforce this for real: the engine pre-final gate blocks the final answer (Terminal.error, error="pre_final_evidence_gate_blocked") when a block_final_answer contract is unsatisfied, and it is on by default for the coding domain. The research final-projection gate, by contrast, has final_answer_blocking_enabled set to Literal[False], so for research-domain turns block_final_answer is recorded as a "block_ready_local_fake" intent without actually blocking output.

- [Evidence contracts](/docs/evidence-contracts)
- [Boundaries](/docs/boundaries)
