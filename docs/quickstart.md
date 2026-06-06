# Quickstart

A truthful first pass through the current source tree and runtime architecture.

Use Homebrew for local runs, or inspect the source checkout when developing the runtime.

## Know the target UX first

This walkthrough shows the architecture and concepts you will encounter when running Magi Agent.

For normal users, the intended flow is `brew install --force-bottle openmagi/tap/magi-agent` followed by `magi-agent serve --port 8080`, then browser-based local onboarding.

### Local runtime

```
brew install --force-bottle openmagi/tap/magi-agent
magi-agent serve --port 8080
open http://localhost:8080/dashboard
```

## Read the runtime map from source

Start by initializing the checkout and opening the docs locally. The architecture, runtime, contracts, tools, and hooks pages explain how composable determinism works before you edit runtime code. The local `start` command is source fallback only: a docs/development server, not live runtime activation.

### Terminal

```
npm install
npm run magi -- init
npm run magi -- doctor
npm run magi -- start
# Visit http://localhost:3000/docs in a browser
```

## Inspect the current install contract

Before adding install instructions, verify the package entrypoints that exist. The root `magi` npm script is the current local source-checkout CLI, while `packages/openmagi` remains cloud-only.

### Terminal

```
npm run magi -- --help
npm run magi -- doctor
cat packages/openmagi/package.json
```

## Verify Source Before Claim

Use the overview example as the first conceptual quickstart. The runtime selects a source-verified workflow, creates a policy snapshot, projects only allowed document refs, records source receipts, links claim state to source spans, validates before each boundary, and projects only governed output.

The same pattern applies to product specs, market reports, code review evidence, legal citations, test results, delivery receipts, and approval-gated side effects.

- before context projection
- before tool execution
- after ToolHost receipt creation
- before child result import
- before next-step summary
- before memory write
- before Slack draft
- before artifact publication
- before final output

## End-to-end worked example

You ask Magi Agent: "Read the uploaded product spec and competitor pricing table. Write a pricing comparison with citations." Here is what happens inside the runtime, step by step.

## Step 1 - Recipe and harness resolution

The runtime detects a research task. The `openmagi.research` and `openmagi.web-acquisition` recipe packs activate. HarnessEngine resolves evidence contracts scoped to `agent_role='research'`. In a strict source-verified profile, the effective policy requires SourceInspection evidence before factual claims can be projected to the user.

The resolved evidence contract looks like this:

### Resolved evidence contract

```
EvidenceContract(
    id='research.source_inspection',
    triggers=('after_tool_use', 'before_commit'),
    requirements=(
        EvidenceRequirement(
            type='SourceInspection',
            fields={'inspected': EvidenceFieldMatcher(equals=True)},
        ),
    ),
    on_missing='block_final_answer',
    retry_message='Inspect the source document before making claims.',
)
```

## Step 2 - Tool calls and evidence

The model proposes reading the uploaded file. The `beforeToolUse` hook fires and the tool boundary checks permissions: FileRead requires read permission, which is allowed. The tool executes. The `afterToolUse` hook fires and a ToolEvidenceRecord is created with `kind='tool_result'` and `status='ok'`. An evidence extractor produces a SourceInspection record with `inspected=True` linked to the source document.

The resulting evidence record:

### Evidence record

```
EvidenceRecord(
    type='SourceInspection',
    status='ok',
    observed_at=1716840000,
    source=EvidenceSource(kind='tool_trace', tool_name='FileRead'),
    fields={'inspected': True, 'source_ref': 'product_spec_2026.pdf'},
)
```

## Step 3 - Claim and contract evaluation

The model drafts a claim: "Competitor A charges $99/seat." Before the answer can be committed, the evidence contract evaluates. It checks: is there a SourceInspection record with `inspected=True`? Yes - the record from Step 2 matches. Verdict: pass. The claim is supported and can proceed.

If the model had claimed something without reading the source, the contract verdict would be `missing`. The enforcement boundary logs an audit event and sends the `retry_message` back to the model: "Inspect the source document before making claims." The model then reads the source and tries again — this is the repair flow.

## Step 4 - Output projection

The `beforeCommit` hook fires. All evidence contracts are re-evaluated against the full evidence ledger. If all verdicts pass, the output is projected to the user with citation references. If any verdict is `missing` or `failed`, the enforcement action determines what happens: `audit` logs and allows, while `block_final_answer` blocks the response and uses the retry message to guide repair.

The user receives: "Based on the product spec (Section 3.2), Competitor A charges $99/seat [source: product_spec_2026.pdf, page 12]." The evidence ledger records the full chain: tool call, source inspection, claim, contract verdict, and projected output.

## What each system contributed

Each layer of the runtime handled a different concern in this scenario:

- Recipe packs (`openmagi.research`) — selected the evidence contracts and hooks for research tasks
- Harness engine — resolved which contracts apply based on `agent_role='research'` and `spawn_depth=0`
- Hook bus — dispatched `beforeToolUse` (permission), `afterToolUse` (evidence), and `beforeCommit` (contract evaluation) hooks
- Evidence ledger — recorded ToolEvidenceRecord and SourceInspection records with source refs
- Evidence contracts — required SourceInspection evidence before claims could proceed
- Enforcement boundary — evaluated contract verdicts and determined audit/block/pass actions
- Output projection — allowed only supported claims with citation references to reach the user
