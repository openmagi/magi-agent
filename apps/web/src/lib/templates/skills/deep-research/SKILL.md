---
name: deep-research
description: "Single-iteration research execution: SCOPE → SEARCH → COLLECT → SYNTHESIZE → DELIVER. Called by deep-research-loop for multi-iteration research, or standalone for Quick mode. All web searches use the web-search skill (web-search.sh + firecrawl.sh)."
user_invocable: true
metadata:
  author: openmagi
  version: "3.0"
---

# Deep Research — Single Iteration Execution

Execute one complete research iteration. Can run standalone (Quick mode) or as part of `deep-research-loop` (Standard/Deep/xDeep/xxDeep).

**Autonomy Principle:** Operate independently. Do not ask the user for permission at each phase — execute and deliver.

## When Called Standalone (Quick Mode)

User says "간단히 조사해줘", "quick research", "빠르게 알아봐" → run the full pipeline below in one session, no cron, no review loop.

## When Called by deep-research-loop

You receive context:
- `scope.md` — sub-questions and boundaries (skip SCOPE phase if this exists)
- `review-{n}.md` — previous review feedback with action items (if iteration > 1)
- `state.json` — current iteration number, mode

**If review feedback exists:** Your primary goal is addressing the action items. Focus searches on gaps identified by the reviewer. Do not redo work that was already verified.

## Pipeline

```
SCOPE → SEARCH → COLLECT → SYNTHESIZE → DELIVER
```

## Phase: SCOPE

Decompose the research question before searching.

1. **Core question**: What exactly is being asked?
2. **Sub-questions**: Break into 3-7 independent angles (more for Deep+)
3. **Boundaries**: What's in scope vs out of scope?
4. **Audience**: Technical depth level (infer from user context)
5. **Language strategy**: Which languages to search in (Korean, English, etc.)

Save to `scope.md`:

```markdown
# Research Scope

## Core Question
[precise statement]

## Sub-Questions
1. [angle 1]
2. [angle 2]
...

## Boundaries
- In scope: [...]
- Out of scope: [...]

## Search Strategy
- Languages: [Korean, English, ...]
- Key domains to check: [...]
```

## Phase: SEARCH

**All searches use `web-search.sh` from the web-search skill.**

Expand each sub-question into 2-3 search variants:

```bash
# Core topic
web-search.sh "query variant 1"
web-search.sh "query variant 2"

# Alternative angle
web-search.sh "different perspective query"
```

**Query expansion strategies:**
- **Synonyms**: "drawbacks" / "limitations" / "cons"
- **Specificity ladder**: Broad → narrow on promising angles
- **Perspective diversity**: Proponents AND critics
- **Recency**: Include current year for up-to-date results
- **Language diversity**: Search Korean AND English when relevant
- **Contrarian**: Explicitly search for opposing viewpoints

**Search volume by mode:**

| Mode | Searches per Iteration |
|------|----------------------|
| Quick | 5-8 |
| Standard | 8-12 |
| Deep | 12-18 |
| xDeep/xxDeep | 15-25 |

**After review feedback (iteration > 1):**
- Focus 60% of searches on action items from review
- Use 40% for strengthening existing findings
- Search specifically for claims flagged as unverified/contradicted

**Process results immediately:** After each search, note:
- High-authority sources (official docs, peer-reviewed, established publications)
- Contrarian views that challenge initial assumptions
- Data-backed claims (benchmarks, case studies, statistics)
- URLs worth scraping for full content

## Phase: COLLECT

Scrape the most promising URLs for full content:

```bash
firecrawl.sh scrape "https://example.com/article"
```

**Source selection (in priority order):**
1. Official documentation and specs
2. Peer-reviewed or well-cited analyses
3. Firsthand experience reports (migration stories, production usage)
4. Recent benchmark data or performance comparisons
5. Industry analyst reports

**Skip scraping when:**
- Search snippet already contains the needed data point
- Source is behind a paywall or login wall
- Source is low signal-to-noise (random forum threads)

**Scrape volume by mode:**

| Mode | Pages per Iteration |
|------|-------------------|
| Quick | 3-5 |
| Standard | 5-8 |
| Deep | 8-12 |
| xDeep/xxDeep | 10-15 |

## Phase: SYNTHESIZE

Connect insights across sources:

1. **Identify patterns**: Themes emerging across sources
2. **Surface non-obvious insights**: What sources collectively imply but none states directly
3. **Map trade-offs**: Every option has costs — make them explicit
4. **Apply context**: How findings relate to user's specific situation
5. **Acknowledge gaps**: What couldn't be determined
6. **Resolve contradictions**: When sources conflict, explain why and assess which is more credible

## Phase: DELIVER

Output the report as `draft-{n}.md` (or directly to user for Quick mode).

### Report Format

```markdown
# [Research Title]

## Executive Summary
[2-4 sentences: key finding + recommendation]

## Key Findings

### 1. [Finding Title]
[Evidence-backed analysis]
- Source: [citation with URL]
- Confidence: High/Medium/Low

### 2. [Finding Title]
...

## Comparison Table (if applicable)
| Criterion | Option A | Option B |
|-----------|----------|----------|

## Trade-offs and Considerations
[Context-dependent factors]

## Recommendation
[Specific, actionable recommendation with reasoning]

## Limitations
[What couldn't be determined, potential biases, gaps]

## Sources
[1] [Title] — [URL]
[2] [Title] — [URL]
...
```

**Formatting rules:**
- Comparison tables when comparing 2+ options
- Bold key takeaways within paragraphs
- Inline citations as [1], [2], etc.
- Source URLs in Sources section
- Write in the language the user used (Korean or English)
- Every major claim must have a citation
- Mark confidence levels on key findings

### sources.json Update

After delivering, update `sources.json`:

```json
[
  {
    "url": "https://...",
    "title": "...",
    "type": "official_docs|research|blog|news|forum",
    "credibility": "high|medium|low",
    "claimsSupported": ["claim1", "claim2"],
    "accessedAt": "ISO timestamp"
  }
]
```

## Adaptive Behavior

**Poor search results:**
- Reformulate with different keywords
- Try different language
- Broaden or narrow scope
- Scrape known authoritative sites directly

**Source conflicts:**
- Present both sides with evidence strength
- Investigate WHY they conflict (different contexts, outdated data, different metrics)
- Default to more authoritative or recent source

**Topic too broad:**
- For Quick: narrow autonomously to most impactful angle
- For Standard+: scope.md should already define boundaries; stay within them

**Time-sensitive topics:**
- Include year/month in search queries
- Flag potentially outdated findings
- Prefer recent sources over older ones
