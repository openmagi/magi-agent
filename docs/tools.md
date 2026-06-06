# Tools

Tools are part of the runtime contract, not just model suggestions.

## Tool Model

The model proposes tool calls. The runtime owns execution, permission checks,
workspace isolation, receipts, and projection. A tool result should not become a
trusted claim until the relevant contract or hook accepts the evidence.

## First-party surfaces

Magi Agent includes first-party surfaces for these work classes. Availability
depends on install extras, feature flags, credentials, and runtime mode.

- file read/write/edit/patch workflows;
- grep, glob, repository map, diagnostics, and verification;
- web search/fetch and source inspection;
- browser-oriented research;
- document and spreadsheet authoring;
- memory and knowledge search/write;
- scheduling and mission/task tracking;
- artifact delivery;
- child/delegated work boundaries;
- runtime health and evidence reporting.

See [First-party packs](first-party-packs.md) for the public pack map and
[Harnesses](harnesses.md) for the evidence contracts behind these surfaces.

## Native Plugin Categories

Native plugin surfaces group built-in tools by work class:

- artifacts;
- browser;
- coding;
- documents;
- knowledge;
- missions;
- scheduled work;
- skills;
- source ledger;
- subagents;
- taskboard;
- web.

These categories are capability groups, not automatic authority grants. A
recipe or runtime mode still decides which categories can be used for a run.

## Common Tool Patterns

Reliable runs usually chain tools:

1. Discover sources with search, grep, glob, or repository map.
2. Read the source before summarizing, citing, or editing it.
3. Write or mutate only inside the configured workspace boundary.
4. Verify deterministic claims with tests, calculations, health checks, or
   source spans.
5. Deliver artifacts before claiming they are attached or ready.
6. Project a final answer that references public-safe evidence.

## Receipts

Tool calls that affect state should write receipts. A receipt should be
public-safe and include enough digest-level evidence to verify what happened
without exposing secrets or raw private payloads.

Useful receipt fields include:

- tool name and safe input summary;
- workspace-relative path, source URL, artifact id, or external handle;
- content digest or output digest when full output is private;
- start and finish timestamps;
- success, failure, or blocked status;
- user approval id when approval was required.

## Approvals

Use approval gates for tools that mutate files, call external services, spend
money, write channels, or operate credentials.

## Tool Safety Checklist

- Scope file tools to the intended workspace root.
- Prefer read-only mode for planning and review.
- Keep external credentials in environment variables or a secret manager.
- Require approval for channel sends, purchases, destructive edits, or broad
  API writes.
- Redact secrets and private paths from public events.
- Keep logs useful enough for audit without exposing raw private payloads.
