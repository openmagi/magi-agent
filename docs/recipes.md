# Recipes

Recipes are the public way to describe reusable agent workflows. A recipe names
the work class, selects the policy and harness surfaces that apply, declares the
allowed tools, and records which evidence must exist before output can be
trusted.

Think of a recipe as "plan-as-data": it is not a prompt snippet that asks the
model to behave. It is a governed contract that the runtime can compile,
validate, materialize, and project through public-safe events.

## Recipe model

A useful recipe defines:

- a stable recipe id and version;
- the work role, such as research, coding, office automation, or browser
  inspection;
- the tool categories the model may request;
- permission ceilings and approval requirements;
- evidence requirements for claims, mutations, calculations, deliveries, and
  child results;
- validators and repair policy;
- projection policy for user-visible output;
- safe metadata for UI, audit, and saved workflow reuse.

Runtime-private inputs, raw tool output, hidden prompts, credentials, private
paths, and provider payloads do not belong in recipe metadata. Public recipe
projection should use identifiers, digests, reason codes, and short safe labels.

## Selection flow

Recipe selection usually follows this flow:

1. The user request, CLI mode, route, or explicit recipe reference proposes one
   or more candidate recipes.
2. The recipe compiler checks ids, versions, digests, dependencies, runtime
   contract compatibility, and hard invariants.
3. The runtime builds an effective policy snapshot from the admitted recipes.
4. Tool, evidence, approval, repair, and projection behavior is derived from the
   snapshot.
5. A public `recipe_selection` event may show requested, applied, or omitted
   recipes with reason codes.

If an explicitly requested recipe is malformed, disabled, unauthorized,
incompatible, or missing a dependency, it should be omitted with a public reason
code instead of silently widening authority.

## Composition rules

Multiple recipes can be composed when their contracts are compatible. Composition
should narrow authority or add evidence; it should not smuggle in broader tool
access.

Good composition patterns:

- adding a source-proof harness to a research workflow;
- adding a delivery-receipt requirement to an office automation workflow;
- adding a human approval gate to a file mutation workflow;
- adding a coding evidence gate before completion claims;
- adding output-budget references for long automation results.

Blocked composition patterns:

- raw private config projected as recipe metadata;
- duplicated non-idempotent hooks;
- grant and deny collisions;
- unbounded retry loops;
- evidence weakening;
- implicit recipe fallback when an explicit required recipe was rejected.

## Saved workflows

A saved workflow can be represented as a recipe-backed command. The saved entry
should keep:

- workflow id and version;
- owner-safe reference;
- source digest;
- compatible runtime contract version;
- promotion history;
- selected recipe refs;
- policy snapshot digest.

Invoking the saved workflow should re-materialize and re-validate the recipe
instead of trusting stale compiled state.

## First-party recipes

Magi Agent includes first-party recipes and recipe contracts for these public
work classes:

- Research: `openmagi.research` style selection, research agents, and research
  child runner recipes. Governs source proof, claim graph, synthesis,
  cross-review, and final projection.
- Coding: coding mutation, coding evidence gate, coding subagents, and coding
  ownership manifest. Governs read-before-edit, stale rejection, patch/diff/test
  evidence, role-scoped child work, and false-success blocking.
- General automation: `automation.*` presets and package boundaries. Governs
  planning, research, files, office, browser inspection/action, scout work,
  approvals, and receipts.
- Memory: memory recall and memory write recipes. Governs recall authority,
  write boundaries, compaction, and source authority.
- Self-improvement: review and promotion recipes. Governs eval capture, review
  gates, rollback, and drift watch.
- Learning usage: learning usage recipes. Governs local evaluation and safe
  usage contracts.

First-party recipes are public docs and contract examples, not hosted deployment
instructions. They should remain usable for local, source, and self-hosted
runtime operators.

## Authoring checklist

- Use a public recipe id such as `vendor.workflow-name`.
- Keep versions explicit.
- Prefer tool categories over raw implementation details.
- Declare the evidence that must exist before a claim can be projected.
- Require approval for mutation, delivery, spend, high-authority credentials,
  browser actions, and external side effects.
- Use public reason codes for blocked or omitted behavior.
- Keep recipe output digest-safe.
- Test composition with both allowed and rejected cases.

## Related docs

- [Harnesses](harnesses.md)
- [First-party packs](first-party-packs.md)
- [Runtime](runtime.md)
- [Tools](tools.md)
- [Contracts](contracts.md)
