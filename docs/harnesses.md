# Harnesses

Harnesses are reusable runtime contracts that make a workflow checkable. A
harness does not replace the model. It defines boundaries around the model:
which actions are allowed, which evidence is required, when repair is possible,
and what can become public output.

## Harness lifecycle

A governed run normally passes through these harness stages:

1. **Admission.** Select a recipe, validate dependencies, freeze policy, and
   reject incompatible authority.
2. **Context projection.** Build the model-visible packet from allowed request,
   session, memory, source, and workspace refs.
3. **Tool boundary.** Route tool proposals through permission, workspace,
   approval, and provider policy.
4. **Evidence capture.** Record source, file, calculation, test, approval,
   browser, delivery, artifact, or child-result receipts.
5. **Intermediate validation.** Check child output, summaries, memory writes,
   and artifact drafts before they become next-step context.
6. **Repair.** Retry, ask, inspect another allowed source, downgrade wording,
   or block the run when evidence is missing.
7. **Final projection.** Render only public-safe, supported output and the
   relevant receipt refs or blockers.

## Harness matrix

- Research determinism: blocks unsupported research claims. Typical evidence:
  opened source proof, snapshot digest, span refs, claim graph, acceptance
  criteria, verifier result.
- Coding: blocks false coding completion claims and unsafe mutations. Typical
  evidence: read ledger, stale-read check, patch receipt, diff summary, test
  run, rollback proof, diagnostics.
- General automation: keeps broad automation observable, approved, and
  reversible. Typical evidence: control request, approval receipt, path/shell
  decision, browser artifact, spreadsheet evidence, delivery receipt.
- Scheduler: models due-work checks without hidden background authority. Typical
  evidence: schedule ref, lease digest, tick decision, due turn ref, delivery
  decision.
- Background task: represents long-running work without promising invisible
  execution. Typical evidence: task ref, checkpoint ref, resume approval,
  completion projection.
- Memory: scopes recall and write behavior to source authority. Typical
  evidence: namespace ref, source refs, write boundary, compaction digest.
- Meta-orchestration: accepts or rejects delegated child work. Typical evidence:
  child envelope, role, evidence refs, inspection verdict, final assembly plan.
- Browser: separates inspection from side-effectful browser actions. Typical
  evidence: page artifact ref, screenshot digest, action decision, approval
  receipt.
- Office automation: makes generated documents and spreadsheets auditable.
  Typical evidence: schema check, formula presence, reconciliation totals, write
  evidence, delivery ref.

## Research harness

Research harnesses treat source-sensitive claims as evidence-bearing objects.
Reliable research needs more than a URL in the final answer.

The public contract expects:

- action proof for research verbs such as searched, read, reviewed, compared,
  confirmed, analyzed, and summarized;
- opened-source proof with snapshot, digest, timestamp, and citeable span refs;
- rejection of URL-only citations and unopened sources;
- freshness checks when the claim depends on current information;
- claim graph support mapping;
- weak-claim downgrade;
- unsupported-claim blocking;
- acceptance criteria extraction;
- child evidence envelope acceptance before parent synthesis;
- final projection that omits raw source bodies and private tool payloads.

Repair actions can inspect another allowed source, request clarification,
downgrade a claim, remove the claim, or report the missing work.

## Coding harness

Coding harnesses model code work as an evidence-producing transaction. The
runtime should not project "fixed", "tested", or "complete" unless the required
evidence exists.

The public contract expects:

- read-before-edit and stale-read rejection;
- patch, file change, and mutation receipts;
- diff evidence before a completion claim;
- test or diagnostic evidence for verification claims;
- safe shell/test-run policy;
- bounded repair loops;
- coding subagent roles for inspection, review, and implementation;
- code intelligence reports using public refs and redacted diagnostics;
- final projection that can downgrade unsupported success claims.

## General automation harness

General automation is intentionally broader than coding or research, so it
needs explicit boundaries. The harness owns:

- plan/act transitions;
- question and approval tools;
- path and external-directory policy;
- shell policy and shell receipts;
- browser evidence and side-effect decisions;
- spreadsheet read, validation, write, and delivery evidence;
- output-budget references for large artifacts;
- background task projection;
- public control and event projection;
- package manifests and tool projection for automation packs.

The harness should return a concrete artifact, receipt, control request, or
blocker. A generated promise that work will happen later is not automation.

## Scheduler harness

Scheduler contracts keep periodic work explicit. A schedule tick should produce
a public-safe decision describing whether due work was found and whether
execution or delivery was allowed.

Useful public fields include:

- schedule or source ref;
- request digest;
- owner/session-safe digests;
- lease ref or lease decision;
- due turn refs;
- reason codes;
- delivery status;
- authority flags.

Scheduler docs should describe local and self-hosted contracts only. Keep
deployment-specific rollout internals out of public OSS docs.

## Verification

Harness changes should be covered with fixture tests that prove:

- private text, private paths, credentials, and raw provider payloads are not
  projected;
- default-off authority stays default-off until explicitly enabled;
- public projections use refs, digests, statuses, and reason codes;
- rejected cases fail closed;
- supported cases produce the expected evidence or event payload.

## Related docs

- [Recipes](recipes.md)
- [First-party packs](first-party-packs.md)
- [Streaming events](streaming-events.md)
- [Contracts](contracts.md)
- [Security](security.md)
