---
name: shorts-studio
description: Use when the user asks to create YouTube Shorts, Instagram Reels, TikTok-style vertical videos, short-form video scripts, storyboards, thumbnails, titles, hashtags, or social video packs from a topic or brief.
metadata:
  author: openmagi
  version: "1.0"
  user_invocable: true
---

# Shorts Studio

Create an Open Magi-native short-form video production pack from a topic or brief. Use platform integrations for Gemini image and video generation, store the work under `/workspace/shorts-studio/`, and deliver finished artifacts with `FileDeliver`.

## Rules

1. Use `integration.sh`; do not ask the user for raw model API keys.
2. Default to one shippable 9:16 MP4 plus a metadata pack. Keep multi-clip assembly out of scope unless a separate media worker is available.
3. Save every run in `/workspace/shorts-studio/{YYYYMMDD-HHMMSS}-{slug}/`.
4. Deliver user-facing files with `FileDeliver(target="chat")` or the `file-send` skill. Do not finish by only reporting a local path.
5. Do not auto-post to YouTube, Instagram, TikTok, X, or other channels unless the user explicitly asks and the matching social skill/integration is available.

## Defaults

| Field | Default |
|-------|---------|
| Platform | YouTube Shorts |
| Language | Korean, unless the user requests another language |
| Duration | 20-35 seconds |
| Aspect ratio | 9:16 |
| Structure | Hook, 4-6 beats, final payoff or CTA |
| Tone | Fast, clear, visual-first, suitable for silent autoplay |

Ask at most one clarifying question only when a missing constraint would materially change the result. Otherwise infer and proceed.

## Output Files

```
/workspace/shorts-studio/{run}/
- brief.json
- scenario.json
- prompts.md
- thumbnail.png
- shorts-final.mp4
- metadata.json
```

If video generation cannot complete, still deliver `scenario.json`, `prompts.md`, and any generated image files.

## Scenario Schema

Write `scenario.json` before calling media tools:

```json
{
  "title": "2-line short title",
  "platform": "youtube-shorts",
  "language": "ko",
  "duration_seconds": 28,
  "concept_type": "what-if | ranking | before-after | challenge | explainer | product",
  "hook": "first 1-2 seconds",
  "visual_identity": {
    "style": "photorealistic, cinematic, high contrast",
    "subject": "consistent subject or product",
    "setting": "consistent environment",
    "negative_prompt": "low quality, watermark, illegible text"
  },
  "beats": [
    {
      "number": 1,
      "duration_seconds": 5,
      "scene_ko": "Korean scene description",
      "on_screen_text": "short overlay text",
      "voiceover_ko": "voiceover line",
      "visual_prompt_en": "English visual prompt for this beat",
      "motion_prompt_en": "English motion prompt for this beat"
    }
  ],
  "image_prompt_en": "single vertical thumbnail/keyframe prompt",
  "video_prompt_en": "single cohesive 9:16 video prompt combining all beats",
  "seo": {
    "youtube_title": "title with #shorts",
    "description": "short description",
    "hashtags": ["#shorts"],
    "tags": ["tag"]
  }
}
```

## Workflow

### 1. Create Run Directory

Use a short slug from the topic:

```bash
RUN_DIR="/workspace/shorts-studio/$(date +%Y%m%d-%H%M%S)-topic-slug"
mkdir -p "$RUN_DIR"
```

Save the user's brief as `brief.json`. Include assumptions you made.

### 2. Write Scenario

Create `scenario.json` and `prompts.md`. The scenario must include:

- A hook that works in the first 1-2 seconds.
- 4-6 visual beats with short on-screen text.
- English `image_prompt_en` and `video_prompt_en`.
- Upload metadata: title, description, tags, hashtags.

### 3. Generate Thumbnail or Keyframe

Use Gemini image generation through the platform proxy:

```bash
integration.sh gemini-image/generate '{
  "prompt": "Vertical 9:16 YouTube Shorts thumbnail, bold clear subject, no tiny text, high contrast, ...scenario image_prompt_en..."
}' > "$RUN_DIR/image-response.json"
```

Decode the returned base64 image and save it as `thumbnail.png`:

```bash
node -e '
const fs = require("fs");
const data = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
fs.writeFileSync(process.argv[2], Buffer.from(data.image, "base64"));
' "$RUN_DIR/image-response.json" "$RUN_DIR/thumbnail.png"
```

### 4. Generate Video

Start one async 9:16 video job:

```bash
integration.sh gemini-video/generate '{
  "prompt": "...scenario video_prompt_en...",
  "aspectRatio": "9:16",
  "resolution": "720p"
}' > "$RUN_DIR/video-start.json"
OPERATION_ID=$(node -e 'const fs=require("fs"); console.log(JSON.parse(fs.readFileSync(process.argv[1],"utf8")).operationId)' "$RUN_DIR/video-start.json")
```

If a strong thumbnail was generated, use it as the first frame:

```bash
IMAGE_B64=$(base64 < "$RUN_DIR/thumbnail.png" | tr -d "\n")
integration.sh gemini-video/generate "{
  \"prompt\": \"...scenario video_prompt_en...\",
  \"aspectRatio\": \"9:16\",
  \"resolution\": \"720p\",
  \"image\": \"$IMAGE_B64\",
  \"imageMimeType\": \"image/png\"
}" > "$RUN_DIR/video-start.json"
OPERATION_ID=$(node -e 'const fs=require("fs"); console.log(JSON.parse(fs.readFileSync(process.argv[1],"utf8")).operationId)' "$RUN_DIR/video-start.json")
```

Poll every 30 seconds:

```bash
integration.sh "gemini-video/status?operation=${OPERATION_ID}" > "$RUN_DIR/video-status.json"
integration.sh "gemini-video/download?operation=${OPERATION_ID}" > "$RUN_DIR/video-download.json"
VIDEO_URI=$(node -e 'const fs=require("fs"); console.log(JSON.parse(fs.readFileSync(process.argv[1],"utf8")).videoUri || "")' "$RUN_DIR/video-download.json")
[ -n "$VIDEO_URI" ] && curl -L "$VIDEO_URI" -o "$RUN_DIR/shorts-final.mp4"
```

When a `videoUri` is returned, download it to `shorts-final.mp4`.

### 5. Package Metadata

Write `metadata.json`:

```json
{
  "title": "display title",
  "youtube_title": "SEO title #shorts",
  "description": "upload description",
  "hashtags": ["#shorts"],
  "tags": ["..."],
  "files": {
    "video": "shorts-final.mp4",
    "thumbnail": "thumbnail.png",
    "scenario": "scenario.json",
    "prompts": "prompts.md"
  }
}
```

### 6. Deliver

Deliver the MP4 if it exists. Also deliver `metadata.json` or summarize it inline:

```text
FileDeliver(target="chat", path="/workspace/shorts-studio/{run}/shorts-final.mp4")
```

If the user is in a web/app channel, include the returned `[attachment:...]` marker in the final response.

## Quality Checks

Before delivery:

- Confirm `scenario.json` is valid JSON.
- Confirm `metadata.json` is valid JSON.
- Confirm MP4 exists and has nonzero size when video generation succeeds.
- Check title and on-screen text are short enough for mobile.
- Explain any missing artifact and provide the completed script/metadata pack instead of claiming full completion.

## Follow-on Skills

- Use `ad-copywriter` when the user wants paid ad copy variants for the short.
- Use `ad-creative-generator` when the user wants additional static ad creatives.
- Use `meta-social` for Instagram/Facebook organic publishing after the user requests posting.
- Use `twitter` for X posting after the user requests posting.
