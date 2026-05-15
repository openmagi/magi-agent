---
name: auto-optimize
description: Autonomous optimization loop — give a target metric and the bot experiments with parameters to optimize it. Like autoresearch but for any measurable objective. Start with marketing (CAC, ROAS, CPA) using existing ad skills, extensible to any domain.
metadata:
  author: openmagi
  version: "1.0"
---

# AutoOptimize — Autonomous Metric Optimization Loop

Run a continuous experiment loop to optimize a target metric. You modify parameters, measure results, keep what works, revert what doesn't, and repeat — autonomously.

## When to Use

- User asks to "optimize my CAC", "lower my CPA", "improve ROAS automatically"
- User creates or mentions an `OPTIMIZE.md` file
- User says "set up auto-optimization" or "run experiments on my ads"
- A cron job triggers with message containing `auto-optimize`

## Quick Start

When user wants to start optimizing:

1. Help them create `OPTIMIZE.md` in workspace root (see format below)
2. Set up cron: `system.run ["openclaw", "cron", "add", "--name", "auto-optimize", "--cron", "<from OPTIMIZE.md>", "--tz", "<timezone>", "--message", "auto-optimize: run cycle"]`
3. Run the first cycle immediately to establish baseline
4. Confirm setup with user

---

## OPTIMIZE.md Format

This is the user's configuration file. Help them create it if it doesn't exist.

```yaml
# Optimization Target

objective:
  metric: CAC                    # What to optimize (CAC, CPA, ROAS, CTR, CVR, custom)
  direction: minimize            # minimize | maximize
  baseline: null                 # Filled by bot after first measurement
  target: 30.00                 # Optional goal value

evaluation:
  period: 24h                   # Min time per experiment (24h, 48h, 72h, 7d)
  min_samples: 100              # Min events before statistical judgment
  confidence: 0.90              # Confidence threshold (0.80, 0.90, 0.95)
  max_concurrent: 1             # Simultaneous experiments (recommend 1)

levers:
  - name: google_ads.daily_budget
    type: continuous
    range: [50, 200]
  - name: google_ads.bid_strategy
    type: categorical
    options: [MAXIMIZE_CLICKS, TARGET_CPA, MAXIMIZE_CONVERSIONS]
  - name: google_ads.ad_copy
    type: generative
    max_variants: 3
  - name: meta_ads.audience_age
    type: range
    bounds: [18, 65]
  - name: meta_ads.daily_budget
    type: continuous
    range: [30, 150]

guardrails:
  max_daily_spend: 200
  blocked_actions:
    - pause_all_campaigns
    - delete_campaigns
    - delete_ad_sets
  approval_required:
    - budget_change_gt_50pct
    - new_campaign_creation

schedule:
  cron: "0 9 * * *"
  timezone: Asia/Seoul

domain: marketing
```

### Field Reference

| Field | Required | Description |
|-------|----------|-------------|
| `objective.metric` | Yes | Target metric name |
| `objective.direction` | Yes | `minimize` or `maximize` |
| `objective.baseline` | No | Bot fills after first measurement |
| `objective.target` | No | Goal value (optimization continues past it) |
| `evaluation.period` | Yes | Minimum time between experiments |
| `evaluation.min_samples` | Yes | Min data points for statistical judgment |
| `evaluation.confidence` | No | Default 0.90 |
| `evaluation.max_concurrent` | No | Default 1 |
| `levers` | Yes | At least one lever required |
| `guardrails` | No | Recommended for safety |
| `schedule.cron` | Yes | When to run the cycle |
| `domain` | Yes | `marketing` or `custom` |

### Lever Types

- **continuous**: Numeric value within `range: [min, max]`
- **categorical**: One of `options: [A, B, C]`
- **range**: Sub-range within `bounds: [min, max]` (e.g., age targeting)
- **generative**: Bot generates content (ad copy, headlines) — `max_variants` limits how many

---

## The Loop — 5 Phases per Cycle

Every cron trigger (or manual "run cycle" request) executes these phases in order.

### Phase 1: MEASURE

**Goal:** Collect current metric values.

**Marketing domain:**
```bash
# Google Ads performance
integration.sh google-ads/performance?customerId=<ID>&days=<period_days>

# Meta Ads performance
integration.sh meta-ads/insights

# Campaign details
integration.sh google-ads/campaigns?customerId=<ID>
integration.sh meta-ads/campaigns
```

**Processing:**
1. Calculate the target metric from raw data:
   - **CAC/CPA**: total_spend / total_conversions
   - **ROAS**: total_conversion_value / total_spend
   - **CTR**: total_clicks / total_impressions × 100
   - **CVR**: total_conversions / total_clicks × 100
2. Calculate secondary metrics for context
3. Save measurement to `optimize/measurements/YYYY-MM-DD.json`:
   ```json
   {
     "date": "2026-03-12",
     "primary": {"metric": "CAC", "value": 42.30},
     "secondary": {"CTR": 0.023, "CVR": 0.041, "daily_spend": 148.50},
     "campaigns": [{"name": "...", "spend": 80, "conversions": 2, "cpa": 40}],
     "raw_data_source": "google-ads + meta-ads"
   }
   ```

**Custom domain:**
- User specifies a measurement command in OPTIMIZE.md (`measure_command`)
- Bot runs it and parses the output as the metric value

**If first run (no baseline):**
- Set `objective.baseline` in OPTIMIZE.md to current value
- Log: "Baseline established: CAC = $42.30"
- Skip to Phase 5 (no experiment to evaluate yet)

### Phase 2: EVALUATE

**Goal:** Judge the running experiment (if any).

1. Load `optimize/status.json` to find current experiment
2. If no running experiment → skip to Phase 3
3. Load the experiment from `optimize/experiments.jsonl`
4. Compare current measurement vs experiment snapshot (baseline at experiment start)

**Statistical Evaluation:**

```
improvement = (baseline - current) / baseline  # for minimize
improvement = (current - baseline) / baseline  # for maximize

# Standard error estimation (simplified)
se = baseline * 0.1 / sqrt(samples)  # 10% assumed CV
z_score = abs(current - baseline) / se

# Confidence check
if samples >= min_samples AND z_score >= z_threshold(confidence):
    → statistically significant
elif samples < min_samples:
    → insufficient data
else:
    → not significant
```

**z_threshold mapping:** 0.80 → 1.28, 0.90 → 1.645, 0.95 → 1.96

**LLM Judgment (when stats are insufficient):**

If samples < min_samples but evaluation period has elapsed, use qualitative analysis:
- Is there a clear directional trend?
- Are there external confounders (weekday/weekend, holiday, seasonal)?
- How noisy is the data (daily variance)?
- What does the campaign-level breakdown show?

**Verdicts:**

| Verdict | Condition | Action |
|---------|-----------|--------|
| `keep` | Significant improvement | Update baseline, log win |
| `revert` | Significant degradation OR strong negative trend | Restore previous parameters, log loss |
| `extend` | Insufficient data, period not elapsed | Wait for next cycle |
| `inconclusive` | Period elapsed, no significant signal | Revert to safe default, log inconclusive |

**Recording the verdict:**

Update the experiment in `optimize/experiments.jsonl`:
```json
{
  "id": 1,
  "status": "evaluated",
  "evaluated_at": "2026-03-13T09:00:00+09:00",
  "result": {"CAC": 38.50, "CTR": 0.019},
  "samples": 142,
  "improvement": 0.148,
  "p_value": 0.03,
  "verdict": "keep",
  "note": "CAC improved 14.8%, statistically significant at p=0.03"
}
```

### Phase 3: DECIDE

**Goal:** Choose the next experiment.

**Step 1 — Load context:**
- Read `optimize/experiments.jsonl` for full history
- Read MEMORY.md `## AutoOptimize Summary` for learned patterns
- Read OPTIMIZE.md for available levers

**Step 2 — Select lever and hypothesis:**

Priority order for choosing what to test next:
1. **Untested levers** — try each lever at least once before repeating
2. **High-potential levers** — levers that showed improvement in past experiments
3. **Refinement** — narrow in on the best value of a winning lever
4. **Combination effects** — test lever interactions (only after individual tests)

**Step 3 — Design the experiment:**

For each lever type:
- **continuous**: Choose a value in range. Start with midpoint, then binary search toward optimal.
- **categorical**: Test each option sequentially. Start with the one most different from current.
- **range**: Narrow or widen the range. Start with the narrower segment.
- **generative**: Use `ad-copywriter` skill to generate new variants based on learned patterns.

**Step 4 — Formulate hypothesis:**

Write a clear, falsifiable hypothesis:
- Good: "Switching bid strategy from MAXIMIZE_CLICKS to TARGET_CPA will reduce CAC by >10% within 48h"
- Bad: "This should improve things"

### Phase 4: ACT

**Goal:** Execute the parameter change.

**Step 1 — Check guardrails:**

```
FOR each action in planned_changes:
  IF action matches blocked_actions → ABORT, log "blocked by guardrail"
  IF action matches approval_required → MESSAGE user, set pending_approval in status.json, STOP
  IF budget change > max_daily_spend → ABORT, log "exceeds spend cap"
```

**Step 2 — Snapshot current state:**

Record current parameter values for rollback:
```json
{
  "snapshot": {
    "google_ads.bid_strategy": "MAXIMIZE_CLICKS",
    "google_ads.daily_budget": 100
  }
}
```

**Step 3 — Execute change:**

**Marketing domain — use existing skills:**

| Lever | Execution Method |
|-------|-----------------|
| `google_ads.daily_budget` | `integration.sh google-ads/mutate '{"customerId":"...","operations":[{"update":{"campaign":"...","campaignBudget":{"amountMicros":"..."}}}]}'` |
| `google_ads.bid_strategy` | `integration.sh google-ads/mutate '{"customerId":"...","operations":[{"update":{"campaign":"...","biddingStrategy":"..."}}]}'` |
| `google_ads.ad_copy` | Use `ad-copywriter` skill to generate → `google-ads` skill to apply via RSA mutate |
| `meta_ads.daily_budget` | `integration.sh meta-ads/ad_sets/<id> POST '{"daily_budget":"..."}'` |
| `meta_ads.audience_age` | `integration.sh meta-ads/ad_sets/<id> POST '{"targeting":{"age_min":...,"age_max":...}}'` |

**Step 4 — Log experiment start:**

Append to `optimize/experiments.jsonl`:
```json
{
  "id": 2,
  "started_at": "2026-03-13T09:00:00+09:00",
  "hypothesis": "Narrowing age to 25-44 will improve conversion quality and reduce CAC",
  "lever": "meta_ads.audience_age",
  "change": {"from": {"min": 18, "max": 65}, "to": {"min": 25, "max": 44}},
  "snapshot": {"CAC": 38.50, "CTR": 0.019, "daily_spend": 145},
  "guardrails_checked": true,
  "status": "running"
}
```

**Step 5 — Also log with `ad-experiment-tracker`:**

For marketing domain, additionally record in `/workspace/ad-experiments.md` using the `ad-experiment-tracker` skill format. This ensures the broader marketing skill ecosystem stays informed.

### Phase 5: WAIT

**Goal:** Update state and go to sleep.

1. Update `optimize/status.json`:
   ```json
   {
     "active": true,
     "current_experiment": 2,
     "baseline": {"CAC": 38.50},
     "total_experiments": 2,
     "wins": 1,
     "losses": 0,
     "inconclusive": 0,
     "last_run": "2026-03-13T09:00:00+09:00",
     "next_run": "2026-03-14T09:00:00+09:00"
   }
   ```

2. Update MEMORY.md `## AutoOptimize Summary` section (see below)

3. **Report to user** — post a brief cycle summary to the `general` channel (if channel-posting is available):
   ```
   🔄 AutoOptimize Cycle #2 complete
   - Previous experiment: CAC $45.20 → $38.50 (-14.8%) ✅ KEEP
   - New experiment: Testing age 25-44 targeting (hypothesis: improve CVR)
   - Next evaluation: 2026-03-14 09:00 KST
   ```

4. Return to sleep. Next cron trigger runs Phase 1 again.

---

## MEMORY.md Auto-Sync

After every completed experiment (Phase 2 verdict != `extend`), update this section:

```markdown
## AutoOptimize Summary

- **Target:** [metric] ([direction]) | Baseline: [original] → Current: [latest] ([change%])
- **Experiments:** [total] total | [wins] wins | [losses] losses | [inconclusive] inconclusive
- **Top learnings:**
  1. [Most impactful learning] (Exp #X)
  2. [Second learning] (Exp #X)
  3. [Third learning] (Exp #X)
- **Active rules:**
  - [Rule from winning experiment — e.g., "TARGET_CPA > MAXIMIZE_CLICKS for this account"]
  - [Rule from losing experiment — what to avoid]
- **Next direction:** [What the bot plans to test next]
```

**Rules:**
- Max 15 lines
- Only include learnings with clear conclusions (skip inconclusive unless pattern emerges)
- Active rules only from experiments with `keep` or `revert` verdicts
- Remove rules contradicted by newer experiments

---

## User Interaction

### User asks "show optimization status"

Read and present:
1. `optimize/status.json` — current state
2. Latest entry in `optimize/experiments.jsonl` — what's running
3. MEMORY.md summary — overall progress
4. Trend from `optimize/measurements/` — metric over time

### User asks "run cycle now"

Execute the full 5-phase cycle immediately, regardless of cron schedule.

### User asks "pause optimization"

1. Set `active: false` in `optimize/status.json`
2. Disable cron: `system.run ["openclaw", "cron", "edit", "<job-id>", "--disable"]`
3. If experiment is running, keep it running but don't start new ones

### User asks "stop optimization"

1. If experiment running: revert to snapshot values
2. Set `active: false` in `optimize/status.json`
3. Remove cron: `system.run ["openclaw", "cron", "rm", "<job-id>"]`

### User modifies something manually

If user makes ad changes outside the loop:
- On next cycle, MEASURE will pick up the new state
- If a running experiment exists, note the external change and mark experiment `inconclusive` (contaminated)
- Update baseline to current state and continue

### User gives approval for pending action

If `pending_approval` is set in status.json:
1. Execute the pending change
2. Clear `pending_approval`
3. Continue from Phase 4 Step 4 (log experiment)

---

## Workspace File Structure

```
optimize/
├── experiments.jsonl          # Append-only experiment log
├── status.json                # Current loop state
└── measurements/
    ├── 2026-03-12.json        # Daily measurement snapshots
    ├── 2026-03-13.json
    └── ...
OPTIMIZE.md                    # User config (workspace root)
```

Create the `optimize/` directory on first run if it doesn't exist.

---

## Guardrail Enforcement

Guardrails are **hard limits**. The bot cannot override them, even if its reasoning suggests it would be beneficial.

### Built-in Guardrails (always active)

1. **No deleting campaigns/ad sets** — only pause, never delete
2. **Rollback must be possible** — don't make changes that can't be undone
3. **One experiment at a time** (default) — prevent compounding variable confusion
4. **Log everything** — every change must be recorded before execution

### User-Configured Guardrails

From OPTIMIZE.md `guardrails` section:
- `max_daily_spend`: Hard budget cap. Before any budget change, verify total spend won't exceed this.
- `blocked_actions`: Actions the bot must never take. Match against planned action type.
- `approval_required`: Actions that need user confirmation before execution.

### Guardrail Check Pseudocode

```
function checkGuardrails(planned_action, config):
  # Built-in checks
  if planned_action.type in [DELETE_CAMPAIGN, DELETE_AD_SET]:
    return BLOCKED("cannot delete — built-in guardrail")

  if planned_action.irreversible:
    return BLOCKED("irreversible action not allowed")

  # User-configured checks
  for blocked in config.guardrails.blocked_actions:
    if matches(planned_action, blocked):
      return BLOCKED("user guardrail: " + blocked)

  for approval in config.guardrails.approval_required:
    if matches(planned_action, approval):
      return NEEDS_APPROVAL(approval)

  if planned_action.budget_impact:
    new_total = current_daily_spend + planned_action.budget_change
    if new_total > config.guardrails.max_daily_spend:
      return BLOCKED("exceeds max_daily_spend cap")

  return ALLOWED
```

---

## Rollback Protocol

When verdict is `revert`:

1. Load experiment's `snapshot` (pre-change parameter values)
2. Execute the reverse change via integration APIs
3. Verify the revert took effect (re-measure after 1 hour if possible)
4. Log: "Reverted Exp #X: [lever] restored to [original value]"

**If revert fails:**
- Message user immediately: "Failed to revert Exp #X. Manual intervention needed: [details]"
- Set `active: false` in status.json
- Do not start new experiments until user resolves

---

## Domain: Marketing — Skill Integration

The auto-optimize loop orchestrates existing marketing skills:

| Phase | Skills Used | How |
|-------|------------|-----|
| MEASURE | `marketing-report` pattern | Pull Google Ads + Meta Ads data via integration.sh |
| EVALUATE | `ad-experiment-tracker` check | Cross-reference with ad-experiments.md |
| DECIDE | `ad-optimizer` patterns, `creative-analyzer` insights | Load learned patterns for decision context |
| ACT (copy) | `ad-copywriter` | Generate new ad copy when the lever is generative |
| ACT (deploy) | `google-ads`, `meta-ads` | Execute parameter changes via mutate APIs |
| TRACK | `ad-experiment-tracker` start | Record experiment in ad-experiments.md too |

**Important:** The auto-optimize loop doesn't replace these skills — it orchestrates them. Users can still use each skill independently.

---

## Domain: Custom (Future)

For non-marketing use cases, users would define:

```yaml
domain: custom

measure:
  command: "curl -s https://api.myapp.com/metrics | jq '.cac'"
  # OR
  command: "cat /workspace/data/latest-metrics.json | jq '.conversion_rate'"

act:
  # Shell commands to apply parameter changes
  commands:
    pricing.monthly_price: "curl -X PUT https://api.myapp.com/pricing -d '{\"price\": {{value}}}'"
```

This is NOT implemented in v1 — noted here for future extensibility.

---

## Example: Full First-Day Cycle

```
09:00 — Cron triggers "auto-optimize: run cycle"

PHASE 1: MEASURE
  → integration.sh google-ads/performance → $4,230 spent, 94 conversions
  → integration.sh meta-ads/insights → $1,850 spent, 47 conversions
  → CAC = $6,080 / 141 = $43.12
  → First run, no baseline → set baseline to $43.12
  → Saved optimize/measurements/2026-03-12.json

PHASE 2: EVALUATE
  → No running experiment → skip

PHASE 3: DECIDE
  → Available levers: budget, bid_strategy, ad_copy, audience_age
  → No experiment history yet → start with bid_strategy (highest expected impact)
  → Hypothesis: "TARGET_CPA at $35 will reduce CAC by >15% vs MAXIMIZE_CLICKS"

PHASE 4: ACT
  → Guardrail check: bid_strategy change → ALLOWED
  → Snapshot: {bid_strategy: MAXIMIZE_CLICKS, CAC: 43.12}
  → Execute: integration.sh google-ads/mutate (set TARGET_CPA)
  → Logged experiment #1 in experiments.jsonl
  → Logged in ad-experiments.md via ad-experiment-tracker pattern

PHASE 5: WAIT
  → Updated status.json: experiment #1 running
  → Updated MEMORY.md: "AutoOptimize started. Baseline CAC $43.12. Testing TARGET_CPA."
  → Posted to general channel: "🔄 AutoOptimize started — baseline CAC $43.12, testing TARGET_CPA bid strategy"
  → Sleep until 2026-03-13 09:00
```

```
09:00 next day — Cron triggers again

PHASE 1: MEASURE
  → CAC today: $37.80 (based on last 24h)

PHASE 2: EVALUATE
  → Experiment #1: CAC $43.12 → $37.80 = -12.3%
  → Samples: 128 conversions (> min_samples 100) ✅
  → z_score: 2.1 > 1.645 (90% confidence) ✅
  → Verdict: KEEP ✅
  → Baseline updated to $37.80

PHASE 3: DECIDE
  → Bid strategy worked. Next untested lever: audience_age
  → Hypothesis: "Narrowing Meta audience to 25-44 will improve CVR and reduce CAC by >10%"

PHASE 4: ACT
  → Execute targeting change on Meta
  → Logged experiment #2

PHASE 5: WAIT
  → MEMORY.md updated with Exp #1 result
  → Channel post: "✅ Exp #1 worked! CAC -12.3%. Now testing age targeting."
```

---

## Error Handling

| Error | Response |
|-------|----------|
| Integration API fails (Google/Meta) | Log error, skip this cycle, retry next cron |
| OPTIMIZE.md missing or malformed | Message user: "OPTIMIZE.md not found/invalid. Run setup." |
| experiments.jsonl corrupted | Attempt to recover last valid line; if impossible, archive and start fresh |
| Experiment running >3× evaluation period | Mark as `inconclusive` (likely contaminated by external changes) |
| Budget cap would be exceeded | Skip experiment, log "budget cap reached", notify user |
| Revert fails | Pause loop, notify user for manual intervention |
