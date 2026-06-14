# Quickstart

The fastest happy path from install to your first answered task.

## Happy path

The canonical flow is Homebrew, one provider key, then `magi -p`:

```
# 1. Install
brew install --force-bottle openmagi/tap/magi-agent

# 2. Set ONE provider key
export ANTHROPIC_API_KEY=...   # or OPENAI_API_KEY / GEMINI_API_KEY / GOOGLE_API_KEY / FIREWORKS_API_KEY / OPENROUTER_API_KEY

# 3. Ask a no-tools question — the real model answers
magi -p "What is 2+2?"
```

Setting one provider key (or creating `~/.magi/config.toml`) builds a real
model-backed runner. With neither, the CLI falls back to a model-free stub.

For a tool-using task, run the interactive `magi` TUI or a headless `magi -p`
command. When `--permission-mode` is omitted, local CLI runs default to
`bypassPermissions` so tools can execute without approval prompts. Pass
`--permission-mode default` when you want per-tool approval prompts:

```
magi   # interactive, no approval prompts by default
magi -p "Read README.md and summarize the install steps"
```

## Local dashboard

To run the local HTTP API and browser dashboard:

```
magi-agent serve --port 8080
open http://localhost:8080/dashboard
```

## Status: enforcement is audit-only today

The worked example below shows the runtime contract. The example enforcement
behaviors it describes (evidence contracts, `block_final_answer`, source-before-claim
blocking) are audit-only / default-off today, so do not expect them to fire and
block a local run by default. Read them as the contract the runtime is built to
enforce when the boundary layer is enabled.

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
    triggers=('afterToolUse', 'beforeCommit'),
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
