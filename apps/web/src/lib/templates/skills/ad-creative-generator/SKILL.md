---
name: ad-creative-generator
description: Generate ad banner images and short-form video ads using Gemini image generation (Nano Banana 2) and Veo 3.1 Fast, with platform-specific sizing and A/B visual testing
metadata:
  author: openmagi
  version: "1.0"
---

## Part of Marketing Automation System

This skill is part of the **Marketing Automation Cycle** — a unified system of skills that work together:

```
[ANALYZE] → [CREATE] → [DEPLOY] → [TRACK] → [LEARN] → repeat
```

| Stage | Skills | Role |
|-------|--------|------|
| ANALYZE | `marketing-report`, `ad-optimizer`, `creative-analyzer` | Identify what's working and what's not |
| RESEARCH | `audience-research` | Understand target audiences and intent |
| **CREATE** | `ad-copywriter`, **`ad-creative-generator`** ← you are here | **Generate optimized ad copy + visuals** |
| DEPLOY | `google-ads`, `meta-ads` | Apply creatives to live campaigns |
| ENGAGE | `meta-social`, `meta-insights` | Organic content and analytics |
| TRACK | `ad-experiment-tracker` | Record experiments and results |

**This skill's role:** Generate ad banner images and short-form video ads for campaigns, informed by copy from `ad-copywriter` and past experiment performance data.

---

## Image Generation

### Supported Sizes

| Platform | Format | Aspect Ratio | Use Case |
|----------|--------|-------------|----------|
| Google Display | 1200×628 | ~1.91:1 → use `16:9` | Landscape banner |
| Google/Meta | 1080×1080 | 1:1 | Square feed ad |
| Meta Story/Reels | 1080×1920 | 9:16 | Vertical story |

### How to Generate

Use `integration.sh` to call the Gemini image generation API:

```bash
# Generate a new image
integration.sh gemini-image/generate '{
  "prompt": "Professional product photo of [product] on clean white background, studio lighting, commercial photography style"
}'
# Returns: { "image": "<base64>", "mimeType": "image/png" }

# Edit an existing image (e.g., change background)
integration.sh gemini-image/edit '{
  "prompt": "Replace the background with a modern kitchen scene, keep the product in focus",
  "image": "<base64 of original image>",
  "mimeType": "image/png"
}'
# Returns: { "image": "<base64>", "mimeType": "image/png" }
```

**Cost:** 5¢ per image generation or edit call.

### After Generation

1. Decode base64 response → save to `/workspace/ad-creatives/`
2. Name files descriptively: `banner-google-landscape-001.png`, `banner-meta-square-001.png`
3. Send to user via Telegram `sendPhoto` for review

---

## Video Generation

### Supported Formats

| Platform | Aspect Ratio | Resolution | Use Case |
|----------|-------------|------------|----------|
| Meta Reels/Story | 9:16 | 720p | Vertical short-form video |
| YouTube Pre-roll | 16:9 | 720p | Landscape pre-roll ad |
| Instagram Feed | 1:1 | 720p | Square video ad |

### How to Generate (Async — 3 steps)

Video generation takes ~2-3 minutes. Use the async polling flow:

```bash
# Step 1: Start generation
integration.sh gemini-video/generate '{
  "prompt": "Product showcase of [product], modern lifestyle setting, cinematic lighting, 8 seconds",
  "aspectRatio": "9:16"
}'
# Returns: { "operationId": "operations/abc123" }

# Step 2: Poll status (repeat until done=true)
integration.sh gemini-video/status?operation=operations/abc123
# Returns: { "done": false, "progress": "50%" }
# ... wait 30 seconds, poll again ...
# Returns: { "done": true, "videoUri": "https://..." }

# Step 3: Get download URI
integration.sh gemini-video/download?operation=operations/abc123
# Returns: { "done": true, "videoUri": "https://...", "mimeType": "video/mp4" }
```

**Cost:** 50¢ per video generation call. Status checks and downloads are free.

### After Generation

1. Download video from `videoUri` → save to `/workspace/ad-creatives/`
2. Name files: `video-meta-reels-001.mp4`, `video-youtube-preroll-001.mp4`
3. Send to user via Telegram `sendVideo` for review

### Polling UX

While waiting for video generation:
1. Tell the user: "영상 생성 중입니다. 약 2-3분 소요됩니다."
2. Poll every 30 seconds
3. When done, immediately download and send the video

---

## Workflow

### Step 0: Load Context

Before generating visuals:

1. **Past experiments** — Read `/workspace/ad-experiments.md` and MEMORY.md `Ad Experiment Summary` for visual performance data (e.g., "close-up product images +22% CTR vs lifestyle shots")
2. **Ad copy** — If `ad-copywriter` has generated copy, reference headlines/descriptions for text overlay prompts
3. **Creative analysis** — If `creative-analyzer` has been run, check which visual styles perform best
4. **Product context** — From user input, product photos, or MEMORY.md

### Step 1: Determine Creative Brief

Ask or infer:
- **What product/service?** — What's being advertised
- **Which platforms?** — Google Display, Meta Feed, Meta Story, YouTube
- **Image or video (or both)?**
- **How many variations?** — Default: 3-5 images or 1-2 videos per platform
- **Style direction** — Product-focused, lifestyle, minimal, bold colors, etc.

### Step 2: Generate Creatives

#### Image Batch

For each platform format, generate 3-5 variations with different approaches:

| Variation | Style | Prompt Pattern |
|-----------|-------|---------------|
| A | Product close-up | "Close-up product photo of [product], clean background, studio lighting" |
| B | Lifestyle context | "[Product] in use, [target audience demographic], natural setting" |
| C | Bold/graphic | "Bold graphic design with [product], vibrant colors, modern typography" |
| D | Minimal | "Minimalist product shot, single [product] centered, white space" |
| E | Social proof | "[Product] with customer testimonials, real-world usage scenario" |

#### Video

Generate 1-2 video variations:
- **Showcase style** — Product reveal/rotation with dynamic angles
- **Story style** — Problem → Solution narrative featuring the product

### Step 3: Save and Organize

Save all creatives to `/workspace/ad-creatives/` with clear naming:

```
/workspace/ad-creatives/
├── campaign-spring-sale/
│   ├── banner-google-landscape-closeup-001.png
│   ├── banner-google-landscape-lifestyle-002.png
│   ├── banner-meta-square-bold-001.png
│   ├── banner-meta-story-minimal-001.png
│   ├── video-meta-reels-showcase-001.mp4
│   └── video-youtube-preroll-story-001.mp4
```

### Step 4: Present to User

Show all creatives to the user via Telegram with labels:
- Image: `sendPhoto` with caption including variation name and platform
- Video: `sendVideo` with caption

### Step 5: Next Steps

After generating creatives, suggest the pipeline:

1. **Pair with copy:** "Use `ad-copywriter` to generate matching ad copy for these visuals"
2. **Deploy:** "Use `google-ads` or `meta-ads` to apply creatives to campaigns"
3. **Track:** "Use `ad-experiment-tracker` to register visual A/B tests (e.g., close-up vs lifestyle)"
4. **Edit:** "I can edit any image — change backgrounds, adjust styling, add elements"
5. **Iterate:** "Use `creative-analyzer` after 7 days to see which visuals perform best"

---

## Image Editing Workflows

Common editing tasks using `gemini-image/edit`:

| Task | Prompt Pattern |
|------|---------------|
| Background swap | "Replace background with [new scene], keep product sharp" |
| Style transfer | "Apply [style] aesthetic to this product photo" |
| Add text overlay | "Add bold text '[headline]' at top of image" |
| Color adjustment | "Make colors warmer/cooler, increase contrast" |
| Remove elements | "Remove [element] from the image, fill naturally" |

---

## Integration with Other Skills

| Skill | How it feeds into ad-creative-generator | How ad-creative-generator feeds back |
|-------|-----------------------------------------|--------------------------------------|
| `ad-copywriter` | Headlines/descriptions → text overlay prompts, thematic direction | Visuals → paired with copy for full ad units |
| `creative-analyzer` | Top visual patterns → style direction for generation | New visuals → performance comparison data |
| `ad-optimizer` | Underperforming visuals → generate replacements | Fresh creatives → swap for paused ads |
| `audience-research` | Audience demographics → lifestyle/context prompts | Targeted visuals → audience-specific campaigns |
| `ad-experiment-tracker` | Learned visual rules → what styles work | New visual A/B test → hypothesis to track |
| `marketing-report` | Visual performance gaps → what to regenerate | Impact measurement after deployment |
