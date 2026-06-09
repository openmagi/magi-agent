# Image Multimodal Restore — Design

- **Date:** 2026-06-09
- **Repo:** `openmagi/magi-agent` (package `magi_agent`)
- **Base:** `origin/main` (d543937), branch `feat/image-multimodal-restore`
- **Status:** Design — approved for plan

## Problem

The legacy TypeScript runtime forwarded user image blocks all the way to the
model. The current Python ADK runtime accepts image-bearing chat payloads but
**drops the images before the model sees them**. A user who sends an image in a
chat turn gets a text-only completion as if the image was never attached.

The drop is not a single bug — images are discarded at **four layers** of the
live HTTP turn path, and a fully-working image pipeline already exists in the
codebase but is **dead code** (never called on the live path).

This restores image input parity with the TS runtime. **Video generation (Veo)
and video input are explicitly out of scope.**

## Confirmed live path (origin/main)

The live HTTP chat turn does **not** go through `RunnerSessionBoundary` /
`TurnControllerInput` (that path serves the CLI/TUI and tests). The live path is
the **gate5b4c3 shadow generation** path:

```
POST /v1/chat/completions                         transport/chat.py:991
  └─ _extract_last_user_text(payload)             transport/chat.py:~3777   [L1: text-only]
  └─ build user-visible generation request        transport/chat.py:~3631
       └─ Gate5B4C3ShadowGenerationRequest         shadow/gate5b4c3_shadow_generation_contract.py   [L2: no image field]
            └─ build_gate5b4c3_runner_input(...)    shadow/gate5b4c3_runner_input_adapter.py:233      [L3: propagates text only]
                 └─ Gate5B4C3RunnerInput            shadow/gate5b4c3_runner_input_adapter.py:106 (sanitized_user_input: str)
                      └─ message = Content(          shadow/gate5b4c3_live_runner_boundary.py:606      [L4: Part(text=...) only]
                           role="user",
                           parts=[Part(text=_runner_message_text(runner_input))])
                         └─ runner.run_async(new_message=message)            live_runner_boundary.py:656
```

Model: Gemini (default `gemini-3.5-flash` via env), which is vision-capable —
so once image parts reach `Content`, the model will process them.

### Where images die today

| Layer | File / location | Current behavior |
|-------|-----------------|------------------|
| L1 | `transport/chat.py` `_extract_last_user_text()` ~3777 | Iterates `content` array, appends only blocks with a `text` field. Image blocks ignored. |
| L2 | `shadow/gate5b4c3_shadow_generation_contract.py` `Gate5B4C3ShadowGenerationRequest` | Schema has no image field — even if L1 preserved images, there's nowhere to carry them. |
| L3 | `shadow/gate5b4c3_runner_input_adapter.py` `Gate5B4C3RunnerInput` (106), `build_gate5b4c3_runner_input()` (233) | `sanitized_user_input: str` only; adapter propagates text. |
| L4 | `shadow/gate5b4c3_live_runner_boundary.py:606` | Builds `Content(parts=[Part(text=...)])` — text-only Part. |

### Dead but reusable

`magi_agent/runtime/message_builder.py` already contains a complete,
**tested** image pipeline that is called by nothing on the live path:
- `SUPPORTED_IMAGE_MEDIA_TYPES` = `{image/jpeg, image/png, image/gif, image/webp}`
- `_collect_image_blocks()` — extracts/validates image blocks from a message
- `_sanitize_image_block()` — base64 validation + per-image & total byte caps
- `build_current_user_message()` — assembles Anthropic-style multipart content

We **reuse** the sanitization/extraction primitives and **revive** them onto the
live path, rather than writing a second pipeline.

## Design

End-to-end change set, threading image blocks through the four layers and
converting them to ADK parts at L4.

### Image block shape

Input blocks (as the chat payload / TS runtime produce them) are Anthropic-style:

```json
{ "type": "image",
  "source": { "type": "base64", "media_type": "image/png", "data": "<base64>" } }
```

ADK / Gemini wants:

```python
types.Part(inline_data=types.Blob(mime_type="image/png", data=<bytes>))
```

So a thin **converter** translates a sanitized image block → `types.Part`. The
`Blob.data` encoding (raw decoded bytes vs base64 string) is validated against
the installed `google-genai` version during implementation (RED test pins it).

### Layer-by-layer

1. **L1 — `transport/chat.py` (extract + preserve)**
   - Add image-block extraction alongside `_extract_last_user_text()`, reusing
     `_collect_image_blocks()` from `message_builder.py` for validation/caps.
   - Carry the sanitized image blocks into the generation request builder
     (~3631) as a new value.
   - **Do not** modify `_build_gate5b_model_visible_current_turn_text()` /
     `_message_content_to_text()` (~3996/4034). That path produces the
     identity-guard text; images travel on a separate field, keeping the
     guardrail surface unchanged.

2. **L2 — `Gate5B4C3ShadowGenerationRequest` (carry)**
   - Add `sanitized_image_blocks` (default empty) to the request schema. Default
     keeps every existing text-only caller and serialized payload valid.

3. **L3 — `Gate5B4C3RunnerInput` + `build_gate5b4c3_runner_input()` (propagate)**
   - Add a matching image-blocks field to `Gate5B4C3RunnerInput` (default empty).
   - `build_gate5b4c3_runner_input()` copies request image blocks into it.

4. **L4 — `gate5b4c3_live_runner_boundary.py:606` (convert + attach)**
   - Build the first `message` `Content` with the text Part **plus** one
     `Part(inline_data=...)` per image block, via the converter.
   - **Continuation** (`next_message`, ~703) and **finalizer** (~1649) stay
     text-only: the image is sent once on the opening turn; resending it on every
     tool-loop continuation wastes tokens and duplicates context.

### Data flow (after)

```
content[] (text + image blocks)
  → L1 extract: text:str  +  image_blocks:[sanitized]
  → L2 request.sanitized_image_blocks
  → L3 runner_input.sanitized_image_blocks
  → L4 Content(parts=[Part(text=...), Part(inline_data=Blob(...)), ...])  # first turn only
  → runner.run_async → Gemini (vision)
```

## Non-goals

- Video **input** and video **generation** (Veo, etc.).
- The `RunnerSessionBoundary` / `TurnControllerInput` CLI/TUI path (not on the
  live HTTP turn; can be a follow-up for CLI image support).
- Image **URL** sources — only inline base64 blocks, matching the TS runtime.
- Changing the identity-guard text path (L2 guardrail extraction).

## Error handling

- Invalid / malformed / oversized image blocks are **dropped, not fatal** — the
  turn proceeds as text-only. This is the existing `_sanitize_image_block`
  behavior; we inherit it. A payload with bad images must never 500.
- Unsupported media types are dropped (only the 4 supported types pass).
- Total/per-image byte caps from `message_builder.py` are enforced at L1.

## Testing strategy (TDD)

Tests live under `tests/`; framework is `pytest`, run via
`uv run --extra dev pytest tests/<file> -q` with an isolated `MAGI_CONFIG`
(mktemp) to avoid `~/.magi/config.toml` pollution.

RED → GREEN per layer:

1. **Converter unit test** — Anthropic-style block → `types.Part` with correct
   `inline_data.mime_type` and decoded `data`; pins the `Blob.data` encoding.
2. **L1** — chat payload with mixed text+image content yields preserved,
   sanitized image blocks on the request; bad/oversized images dropped.
3. **L2** — `Gate5B4C3ShadowGenerationRequest` round-trips `sanitized_image_blocks`
   (alias + default-empty back-compat).
4. **L3** — `build_gate5b4c3_runner_input()` propagates image blocks into
   `Gate5B4C3RunnerInput`.
5. **L4** — opening `Content` contains text Part + image Part(s); continuation
   and finalizer messages remain text-only.
6. **Regression** — existing `tests/test_priority_a_message_builder.py` stays
   green; a text-only turn produces an unchanged single text Part.

## Risks / open questions (resolve in implementation)

- **`Blob.data` encoding** — bytes vs base64 string for the installed
  `google-genai`. Pinned by the converter RED test before wiring L4.
- **Multiple images** — payload may carry several image blocks; converter and
  L4 must emit one Part each (cap already enforced by `message_builder`).
- **Default-OFF gating?** — confirm whether image attachment should ride behind
  an env flag for staged rollout, or ship on by default (TS parity = on). Plan
  assumes on-by-default with safe drop-on-error; revisit if a gate is wanted.
