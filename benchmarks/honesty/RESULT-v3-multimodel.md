# Honesty bench — the result holds across agent models

The same 3-layer comparison, run with three different AGENT models (OpenAI / Anthropic / Google) under an Opus-4.8 judge. If the pattern were a quirk of one model it would not repeat; it does.

## gpt-5.5 (OpenAI)  (unbacked N=21, backed N=19)

| layer | catches unbacked (recall ↑) | blocks receipt-backed work ↓ |
|---|---|---|
| judge · answer:trusting | 3/21 (14%) | 0/19 (0%) |
| judge · answer:balanced | 3/21 (14%) | 0/19 (0%) |
| judge · answer:neutral | 21/21 (100%) | 19/19 (100%) |
| judge · answer:skeptical | 21/21 (100%) | 19/19 (100%) |
| judge · transcript:balanced | 16/21 (76%) | 0/19 (0%) |
| judge · transcript:neutral | 16/21 (76%) | 0/19 (0%) |
| **evidence-bound** | **21/21 (100%)** | **0/19 (0%)** |

## claude-sonnet-4-6 (Anthropic)  (unbacked N=3, backed N=8)

| layer | catches unbacked (recall ↑) | blocks receipt-backed work ↓ |
|---|---|---|
| judge · answer:trusting | 0/3 (0%) | 0/8 (0%) |
| judge · answer:balanced | 2/3 (67%) | 1/8 (12%) |
| judge · answer:neutral | 3/3 (100%) | 8/8 (100%) |
| judge · answer:skeptical | 3/3 (100%) | 8/8 (100%) |
| judge · transcript:balanced | 1/3 (33%) | 0/8 (0%) |
| judge · transcript:neutral | 2/3 (67%) | 0/8 (0%) |
| **evidence-bound** | **3/3 (100%)** | **0/8 (0%)** |

## gemini-3.1-pro (Google)  (unbacked N=3, backed N=13)

| layer | catches unbacked (recall ↑) | blocks receipt-backed work ↓ |
|---|---|---|
| judge · answer:trusting | 0/3 (0%) | 0/13 (0%) |
| judge · answer:balanced | 0/3 (0%) | 0/13 (0%) |
| judge · answer:neutral | 3/3 (100%) | 13/13 (100%) |
| judge · answer:skeptical | 3/3 (100%) | 13/13 (100%) |
| judge · transcript:balanced | 1/3 (33%) | 1/13 (8%) |
| judge · transcript:neutral | 1/3 (33%) | 1/13 (8%) |
| **evidence-bound** | **3/3 (100%)** | **0/13 (0%)** |

## Pooled across all three models (unbacked N=27, backed N=40)

| layer | catches unbacked (recall ↑) | blocks receipt-backed work ↓ |
|---|---|---|
| judge · answer:trusting | 3/27 (11%, CI [4,28]) | 0/40 (0%, CI [0,9]) |
| judge · answer:balanced | 5/27 (19%, CI [8,37]) | 1/40 (2%, CI [0,13]) |
| judge · answer:neutral | 27/27 (100%, CI [88,100]) | 40/40 (100%, CI [91,100]) |
| judge · answer:skeptical | 27/27 (100%, CI [88,100]) | 40/40 (100%, CI [91,100]) |
| judge · transcript:balanced | 18/27 (67%, CI [48,81]) | 1/40 (2%, CI [0,13]) |
| judge · transcript:neutral | 19/27 (70%, CI [52,84]) | 1/40 (2%, CI [0,13]) |
| **evidence-bound** | **27/27 (100%, CI [88,100])** | **0/40 (0%, CI [0,9])** |

**Read the pooled `blocks receipt-backed work` column:** a suspicious answer-only judge (neutral/skeptical) blocks **all 40** genuinely-completed tasks (CI lower bound 91%), across all three providers; the transcript judge and the receipt gate block ~0 (CI upper bound 9-13%). The confidence intervals do not overlap. Whether prose can separate real work from a bare claim does not depend on which frontier model wrote the prose.

### Honest note on the recall column

The stronger agent models (sonnet, gemini) simply *make far fewer* unbacked claims — asked to compute without a tool, they disclose they can't rather than assert a number (so their unbacked N is only 3 each, vs 21 for gpt-5.5). That is a real, welcome finding — a more honest agent is harder to catch lying because it lies less — but it makes the per-model recall CIs wide. The load-bearing, model-independent result is the `blocks-backed` column (N=40 pooled): prose review cannot vouch for real work without over-blocking, no matter the model.
