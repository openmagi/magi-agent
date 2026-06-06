# Tools

Tools are part of the runtime contract, not just model suggestions.

## First-party surfaces

Magi Agent includes first-party surfaces for:

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

## Receipts

Tool calls that affect state should write receipts. A receipt should be
public-safe and include enough digest-level evidence to verify what happened
without exposing secrets or raw private payloads.

## Approvals

Use approval gates for tools that mutate files, call external services, spend
money, write channels, or operate credentials.

