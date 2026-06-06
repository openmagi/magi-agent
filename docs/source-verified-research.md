# Source-Verified Research

Worked example of a research recipe requiring SourceInspection and WebSearch evidence before claims.

A source-verified research workflow uses evidence contracts to require SourceInspection or WebSearch evidence before factual claims can cross boundaries. The evidence types and contracts are implemented; the recipe execution layer is metadata-only.

## Research scenario

User request: Read the uploaded product spec and competitor pricing table. Answer the competitive positioning questions. If something is not in the documents, say so clearly.

This workflow needs source-verified evidence: every factual claim in the response should trace back to an inspected source. The harness enforces this through evidence contracts that require SourceInspection or WebSearch evidence records.

## Evidence contracts for research

The research harness pack includes hooks for source-authority, claim-citation, and fact-grounding. Evidence contracts require SourceInspection or WebSearch evidence types with status='ok' before claims cross the afterToolUse and beforeCommit boundaries.

These contracts are excerpts from the research domain implementation, which spans ~16,000 lines across source ledger tracking, citation auditing, claim graph management, and multi-layer scope enforcement. The full research harness includes 10 evidence case categories, 15 attachment flags, and 4 authority policies.

### Research evidence contract (Python, uses real types)

```
source_contract = EvidenceContract(
    id='research.source-inspection',
    description='Require source inspection before factual claims',
    triggers=('afterToolUse', 'beforeCommit'),
    requirements=(
        EvidenceRequirement(
            type='SourceInspection',
            fields={
                'inspected': EvidenceFieldMatcher(equals=True),
            },
        ),
    ),
    on_missing='audit',
    retry_message='Inspect the source document before making claims.',
)

web_search_contract = EvidenceContract(
    id='research.web-search',
    description='WebSearch evidence for external claims',
    triggers=('afterToolUse',),
    requirements=(
        EvidenceRequirement(type='WebSearch'),
    ),
    on_missing='audit',
)
```

## Evidence records produced

When the agent inspects a source document, the runtime records an EvidenceRecord with type='SourceInspection', status='ok', and metadata fields. When the agent searches the web, a 'WebSearch' evidence record is created. These records are matched against contract requirements at each trigger point.

The 15 builtin evidence types are: GitDiff, TestRun, CodeDiagnostics, CommitCheckpoint, FileDeliver, ArtifactVerify, DeterministicEvidenceVerifier, WebSearch, KnowledgeSearch, SourceInspection, PlanVerifier, Calculation, DateRange, Clock, TelegramDeliveryAck. Custom types use the custom:PascalCaseName format.

## Scoping to research role

The evidence contract scope restricts these contracts to agent_role='research' and run_on='main'. Child research agents at spawn_depth > 0 can have different scoping via SpawnDepthRange. The default enforcement mode is 'audit' which logs missing evidence without blocking; 'block_final_answer' would prevent the final answer when evidence is missing.

### Research scope (Python)

```
scope = EvidenceContractScope(
    contract_id='research.source-inspection',
    agent_roles=('research',),
    run_on=('main',),
    spawn_depth=SpawnDepthRange(min_depth=0, max_depth=0),
    enforcement='audit',
    opt_out_allowed=True,
    hard_safety=False,
)
```

## What works today

The evidence contract system (EvidenceContract, EvidenceContractScope, EvidenceRecord, EvidenceContractVerdict) is fully implemented and active. HarnessEngine resolves these contracts and the research harness pack (with WebSearch, WebFetch, KnowledgeSearch tools and source-authority/claim-citation/fact-grounding hooks) is part of the default ResolvedHarnessPresetState. The relevant first-party recipe packs are openmagi.research and openmagi.web-acquisition.

The recipe metadata layer (RecipePackManifest with evidenceRefs for source verification) compiles but does not execute. Runtime enforcement comes from the harness and evidence system, not from recipe snapshots.
