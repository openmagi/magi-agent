# First-party Packs

First-party packs are Magi-owned recipe, harness, plugin, and tool surfaces for
common work classes. They make useful agent behavior available without moving
workflow-specific policy into the generic runtime core.

## Pack rules

First-party packs should:

- stay public and self-hostable;
- declare authority through recipes, harnesses, and tool metadata;
- start default-off when they need live providers, external writes, browser
  side effects, delivery, or long-running execution;
- expose digest-safe public projections;
- use approval gates for high-authority work;
- avoid raw secrets, private paths, hidden prompts, raw provider payloads, and
  deployment-specific rollout details.

The core runtime provides common primitives. The pack owns the work-class
semantics.

## General automation presets

The general automation pack exposes these recipe-owned presets:

- `automation.plan`: planning, decomposition, and user questions. Permission
  ceiling: `read`, `meta`. No mutation tools.
- `automation.research`: web and source research for automation tasks.
  Permission ceiling: `read`, `net`, `meta`.
- `automation.files`: workspace file work. Permission ceiling: `read`, `write`,
  `meta`. Workspace writes and external directories require approval.
- `automation.office`: document and spreadsheet work. Permission ceiling:
  `read`, `write`, `meta`. Artifact evidence and delivery receipts apply.
- `automation.browser-inspect`: open, snapshot, and scrape browser pages.
  Permission ceiling: `read`, `net`, `meta`. Inspection only.
- `automation.browser-act`: click, fill, download, and submit browser actions.
  Permission ceiling: `read`, `write`, `net`, `meta`. Side-effectful actions
  require approval.
- `automation.scout`: broad scouting with constrained tools. Permission
  ceiling: `read`, `net`, `meta`. No mutation tools.

Alias metadata cannot escalate non-mutating presets into mutating browser
actions. Public preset projection shows ignored escalation attempts with reason
codes.

## Automation tools

The general automation harness and plugins define public contracts for:

- user questions and resumable answers;
- plan-to-act transitions after approval;
- path access checks;
- external-directory approval receipts;
- shell policy and shell policy receipts;
- browser evidence and side-effect decisions;
- spreadsheet evidence;
- web source receipts;
- background task completion projection;
- output references for large generated results;
- package manifests and package tool projection.

These surfaces are designed so a run can show what happened without exposing
raw tool input, raw browser state, private filesystem paths, or credentials.

## Web acquisition pack

The web acquisition pack separates search, fetch, reader, and browser fallback
operations from provider implementation details.

Public expectations:

- disabled config returns a disabled decision without provider calls;
- untrusted providers are blocked;
- live network access must be explicitly enabled;
- provider names must be allowlisted;
- SSRF-sensitive URLs are blocked before provider calls;
- redirects and blocked markers produce public reason codes;
- source records use safe refs and digests, not raw page bodies.

Use web acquisition for source discovery and source opening. Use the research
harness to decide whether the resulting source proof supports a claim.

## Browser pack

Browser pack actions are split into inspection and action surfaces.

Inspection actions include opening pages, snapshots, scraping, and screenshots.
Action surfaces include click, fill, scroll, download, and submit behavior.
Side-effectful actions should require an approval receipt tied to the request
digest.

Public browser output should include safe URL/title text, artifact refs, content
digests, and reason codes. Raw browser protocol payloads, form values, cookies,
auth headers, and screenshots containing private material must not be projected
as public text.

## Office pack

Office automation uses evidence, not promises. Spreadsheet and document work
should record:

- read evidence for inputs;
- schema checks for expected columns or sections;
- formula presence checks when formulas matter;
- reconciliation totals for numeric claims;
- validation evidence;
- write evidence for generated files;
- delivery claim decisions before claiming a file was sent or attached.

The same pattern applies to other generated artifacts: create the artifact,
record the ref, verify the claim, then project the result.

## Coding pack

The coding pack owns coding-specific reliability behavior:

- read ledger and stale-read rejection;
- mutation intent and patch application;
- diff and test evidence;
- formatter and diagnostics helpers;
- code intelligence projection;
- coding subagent roles;
- repair loops;
- final projection that blocks or downgrades unsupported success claims.

It should not grant mutation authority just because the model asks for it. File
changes, shell commands, and verification claims must pass their contracts.

## Research pack

The research pack owns source-sensitive reasoning:

- local, web, and source-ledger research tool grants;
- scout research profiles;
- research child runner envelopes;
- claim graph construction;
- source proof verification;
- repair planning;
- final projection gates;
- meta-orchestration roles for multi-child synthesis.

Research output should cite opened source refs and span evidence. Discovery,
link text, or model memory alone is not enough for source-backed claims.

## Native plugin catalog

Native plugin surfaces group built-in tools by work class. Public categories
include artifacts, browser, coding, documents, knowledge, missions, scheduled
work, skills, source ledger, subagents, taskboard, and web.

Native plugins should be described as local runtime capabilities. Availability
can depend on install extras, feature flags, credentials, provider trust, and
runtime mode.

## Related docs

- [Recipes](recipes.md)
- [Harnesses](harnesses.md)
- [Automation](automation.md)
- [Tools](tools.md)
- [Integrations](integrations.md)
