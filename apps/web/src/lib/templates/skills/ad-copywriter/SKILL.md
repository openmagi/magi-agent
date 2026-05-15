---
name: ad-copywriter
description: Bulk ad copy generation for Google Ads RSA and Meta Ads with character limit enforcement, past experiment learning, and multi-language support
metadata:
  author: openmagi
  version: "1.0"
---

## Part of Marketing Automation System

This skill is part of the **Marketing Automation Cycle** — a unified system of 10 skills that work together:

```
[ANALYZE] → [CREATE] → [DEPLOY] → [TRACK] → [LEARN] → repeat
```

| Stage | Skills | Role |
|-------|--------|------|
| ANALYZE | `marketing-report`, `ad-optimizer`, `creative-analyzer` | Identify what's working and what's not |
| RESEARCH | `audience-research` | Understand target audiences and intent |
| **CREATE** | **`ad-copywriter`** ← you are here, `ad-creative-generator` | **Generate optimized ad copy + visuals** |
| DEPLOY | `google-ads`, `meta-ads` | Apply copy to live campaigns |
| ENGAGE | `meta-social`, `meta-insights` | Organic content and analytics |
| TRACK | `ad-experiment-tracker` | Record experiments and results |

**This skill's role:** Generate bulk ad copy variations optimized for specific platforms, informed by past experiment data and audience research.

---

## Workflow

### Step 0: Load Context

Before generating any copy, gather context:

1. **Past experiments** — Read `/workspace/ad-experiments.md` if it exists. Check MEMORY.md for the `Ad Experiment Summary` section. Apply learned rules (e.g., "question headlines outperform", "avoid emojis on Meta").
2. **Audience profile** — If `audience-research` has been run, reference the audience profile and copy hints.
3. **Creative analysis** — If `creative-analyzer` has been run, reference the Copy Generation Brief (top performing patterns).
4. **Product/service context** — From user input or MEMORY.md.

### Step 1: Determine Platform & Format

Ask the user which platform(s) to generate for, or infer from context:

**Google Ads RSA (Responsive Search Ads):**
- Headlines: up to 15, each **max 30 characters**
- Descriptions: up to 4, each **max 90 characters**

**Meta Ads:**
- Primary text: **max 125 characters** (visible without "See more")
- Headline: **max 40 characters**
- Description: **max 30 characters**
- Generate **5 variations** per batch

### Step 2: Generate Copy

**Headline types to diversify across:**

| Type | Description | Example |
|------|-------------|---------|
| Question | Starts with a question | "Need Better Results?" |
| Number | Includes specific numbers | "Save 50% This Week" |
| CTA | Direct call to action | "Start Free Today" |
| Benefit | Highlights key benefit | "Faster Than Ever" |
| Social Proof | Leverages credibility | "Trusted by 10K+" |
| Urgency | Creates time pressure | "Ends Tomorrow" |

**Rules:**
- Distribute headline types evenly unless past experiments show a clear winner
- If experiments show a winning type (e.g., question headlines CTR +28%), allocate more to that type (40-50%)
- Never repeat the same structure — each headline must be unique
- Use the target language consistently throughout

### Step 3: Validate Character Limits

**CRITICAL — Every generated copy MUST pass validation:**

For each piece of copy, count characters and verify:
- Google Headlines: ≤ 30 characters (REJECT if over)
- Google Descriptions: ≤ 90 characters (REJECT if over)
- Meta Primary Text: ≤ 125 characters (REJECT if over)
- Meta Headline: ≤ 40 characters (REJECT if over)
- Meta Description: ≤ 30 characters (REJECT if over)

If any copy exceeds the limit, rewrite it shorter. Never present over-limit copy to the user.

### Step 4: Output Format

#### Google RSA Output

```markdown
## Google RSA — [Campaign Name]

**Context:** [Brief description of product/campaign goal]
**Language:** [Target language]
**Learned patterns applied:** [List any experiment-derived rules used]

### Headlines (max 30 chars) × 15

| # | Headline | Type | Chars | Notes |
|---|----------|------|-------|-------|
| 1 | Save 50% This Week Only | Number/Urgency | 23 | Based on Exp #009: number headlines +15% CVR |
| 2 | Ready for Better Results? | Question | 25 | Top pattern from creative analysis |
| ... | ... | ... | ... | ... |

### Descriptions (max 90 chars) × 4

| # | Description | Chars | Notes |
|---|-------------|-------|-------|
| 1 | Get started in minutes. No credit card required. Free trial available now. | 72 | CTA + low-friction |
| ... | ... | ... | ... |
```

#### Meta Ads Output

```markdown
## Meta Ads — [Campaign Name]

**Context:** [Brief description]
**Learned patterns applied:** [List any experiment-derived rules used]

### Variation 1
- **Primary Text** (125 chars max): [text] — [X chars]
- **Headline** (40 chars max): [text] — [X chars]
- **Description** (30 chars max): [text] — [X chars]

### Variation 2
...

### Variation 5
...
```

### Step 5: Next Steps

After generating copy, suggest the pipeline:

1. **Deploy:** "Use `google-ads` or `meta-ads` to apply these variations to your campaigns"
2. **Track:** "Use `ad-experiment-tracker` to register this as an experiment with your hypothesis"
3. **Monitor:** "Use `marketing-report` in 7 days to check performance"
4. **Analyze:** "Use `creative-analyzer` to compare new vs old copy performance"

---

## Multi-Language Support

When the user requests copy in multiple languages:

1. Generate the primary language first
2. Adapt (not translate) for each additional language — maintain cultural nuance
3. Re-validate character limits for each language (character counts vary)
4. Present in separate sections per language

---

## Batch Generation

For large-scale copy generation:

- **Single campaign:** 1 RSA set (15H + 4D) + 1 Meta set (5 variations) = default batch
- **Multi-campaign:** Generate per campaign, clearly labeled
- **A/B testing batches:** Generate 2 thematic sets for the same campaign (e.g., "price-focused" vs "benefit-focused") — recommend tracking with `ad-experiment-tracker`

---

## Integration with Other Skills

| Skill | How it feeds into ad-copywriter | How ad-copywriter feeds back |
|-------|---------------------------------|-----------------------------|
| `creative-analyzer` | Copy Generation Brief → types, patterns, top performers | New variations → performance comparison |
| `ad-optimizer` | Underperformer list → which campaigns need new copy | Fresh copy → replacement for paused ads |
| `audience-research` | Audience profile + copy hints → tone and messaging direction | Targeted copy → audience-specific campaigns |
| `ad-experiment-tracker` | Learned rules → what works/doesn't | New experiment → hypothesis to track |
| `marketing-report` | Performance gaps → where to focus | Impact measurement after deployment |
