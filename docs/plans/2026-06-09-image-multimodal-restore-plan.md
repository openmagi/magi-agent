# Image Multimodal Restore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore image input parity with the legacy TS runtime by threading sanitized image blocks through the four-layer gate5b4c3 live HTTP turn path and attaching them to the ADK `Content` as image parts.

**Architecture:** The live path is `POST /v1/chat/completions` → `_build_user_visible_generation_request` → `Gate5B4C3ShadowGenerationRequest` → `build_gate5b4c3_runner_input` → `Gate5B4C3RunnerInput` → `gate5b4c3_live_runner_boundary` builds `types.Content` → `runner.run_async`. We add a `sanitized_image_blocks` field at the request-turn and runner-input layers, extract+sanitize image blocks at the HTTP layer (reusing the already-present-but-dead `message_builder` image pipeline), and convert them to ADK parts at the `Content` build site. Images ride on the opening user message only; tool-loop continuations and the finalizer stay text-only. Default-ON, drop-on-error.

**Tech Stack:** Python 3.11, Pydantic v2 (frozen, `extra="forbid"`, alias models), Google ADK / `google-genai` `types.Content`/`types.Part`, pytest via `uv run --extra dev pytest`.

**Spec:** `docs/plans/2026-06-09-image-multimodal-restore-design.md`

**Test invariant:** Every `pytest` run is prefixed with an isolated config to avoid `~/.magi/config.toml` pollution:
`MAGI_CONFIG=$(mktemp) uv run --extra dev pytest <args>`

---

## File Structure

- **Create** `magi_agent/shadow/gate5b4c3_image_parts.py` — pure converter from sanitized Anthropic-style image blocks to ADK parts via an injected `part_factory`. No ADK import; fully unit-testable.
- **Modify** `magi_agent/shadow/gate5b4c3_shadow_generation_contract.py` — add `Gate5B4C3ShadowGenerationImageBlock` model + `sanitized_image_blocks` field on `Gate5B4C3ShadowGenerationTurn` (default empty, byte-capped, supported-media-type only).
- **Modify** `magi_agent/shadow/gate5b4c3_runner_input_adapter.py` — add `sanitized_image_blocks` to `Gate5B4C3RunnerInput`; propagate it in `build_gate5b4c3_runner_input`.
- **Modify** `magi_agent/transport/chat.py` — add `_extract_last_user_image_blocks(payload)` (normalize OpenAI `image_url` data-URLs + native Anthropic blocks, then sanitize via `message_builder._collect_image_blocks`); wire the result into `_build_user_visible_generation_request`'s `turn_payload`.
- **Modify** `magi_agent/shadow/gate5b4c3_live_runner_boundary.py` — at the opening `Content` build (line ~606), append image parts from `runner_input.sanitized_image_blocks` using `primitives.Part.from_bytes`.
- **Tests** — `tests/test_gate5b4c3_image_parts.py` (new), and additions to existing `tests/` for contract, adapter, chat extraction, and boundary.

**Reused (already present):** `magi_agent/runtime/message_builder.py` — `_collect_image_blocks(user_message, metadata)` returns a list of `{"type":"image","source":{"type":"base64","media_type","data"}}`; `SUPPORTED_IMAGE_MEDIA_TYPES = {image/jpeg, image/png, image/gif, image/webp}`; per-image and total byte caps are enforced inside `_sanitize_image_block`/`_append_capped_image_block`.

---

## Task 1: Image-blocks → ADK parts converter

**Files:**
- Create: `magi_agent/shadow/gate5b4c3_image_parts.py`
- Test: `tests/test_gate5b4c3_image_parts.py`

This is a pure function. The ADK `Part` factory is injected so the test needs no `google-genai`. The contract: each sanitized block `{"source":{"media_type","data"(base64)}}` becomes one part built by `part_factory(data=<decoded bytes>, mime_type=<media_type>)`. Invalid/non-image entries are skipped (defense-in-depth; upstream already sanitizes).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gate5b4c3_image_parts.py
import base64

from magi_agent.shadow.gate5b4c3_image_parts import image_blocks_to_parts


def _block(media_type: str, raw: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.b64encode(raw).decode("ascii"),
        },
    }


def test_converts_blocks_to_parts_with_decoded_bytes():
    calls: list[tuple[bytes, str]] = []

    def factory(*, data: bytes, mime_type: str):
        calls.append((data, mime_type))
        return ("part", mime_type)

    parts = image_blocks_to_parts(
        [_block("image/png", b"\x89PNG..."), _block("image/jpeg", b"\xff\xd8jpg")],
        part_factory=factory,
    )

    assert parts == [("part", "image/png"), ("part", "image/jpeg")]
    assert calls == [(b"\x89PNG...", "image/png"), (b"\xff\xd8jpg", "image/jpeg")]


def test_skips_malformed_blocks():
    def factory(*, data: bytes, mime_type: str):
        return (data, mime_type)

    parts = image_blocks_to_parts(
        [
            {"type": "text", "text": "nope"},
            {"type": "image", "source": {"type": "base64"}},  # no data/media_type
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "!!notb64!!"}},
        ],
        part_factory=factory,
    )

    assert parts == []


def test_empty_returns_empty():
    assert image_blocks_to_parts([], part_factory=lambda **_: None) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MAGI_CONFIG=$(mktemp) uv run --extra dev pytest tests/test_gate5b4c3_image_parts.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'magi_agent.shadow.gate5b4c3_image_parts'`

- [ ] **Step 3: Write minimal implementation**

```python
# magi_agent/shadow/gate5b4c3_image_parts.py
"""Convert sanitized Anthropic-style image blocks into ADK content parts.

Pure module: the ADK Part constructor is injected via ``part_factory`` so this
has no google-genai dependency and is unit-testable in isolation. Input blocks
are assumed already sanitized upstream (supported media type, valid base64,
byte caps); malformed entries are skipped defensively.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Mapping, Sequence
from typing import Any

PartFactory = Callable[..., Any]


def image_blocks_to_parts(
    blocks: Sequence[object],
    *,
    part_factory: PartFactory,
) -> list[Any]:
    parts: list[Any] = []
    for block in blocks:
        if not isinstance(block, Mapping) or block.get("type") != "image":
            continue
        source = block.get("source")
        if not isinstance(source, Mapping) or source.get("type") != "base64":
            continue
        media_type = source.get("media_type")
        data = source.get("data")
        if not isinstance(media_type, str) or not isinstance(data, str):
            continue
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            continue
        if not raw:
            continue
        parts.append(part_factory(data=raw, mime_type=media_type))
    return parts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MAGI_CONFIG=$(mktemp) uv run --extra dev pytest tests/test_gate5b4c3_image_parts.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add magi_agent/shadow/gate5b4c3_image_parts.py tests/test_gate5b4c3_image_parts.py
git commit -m "feat(gate5b4c3): pure image-block -> ADK part converter"
```

---

## Task 2: Carry image blocks on the request-turn contract

**Files:**
- Modify: `magi_agent/shadow/gate5b4c3_shadow_generation_contract.py` (turn model ~292-332)
- Test: `tests/test_gate5b4c3_shadow_generation_contract_image_blocks.py` (new)

Add a typed image-block model and a `sanitized_image_blocks` tuple field on `Gate5B4C3ShadowGenerationTurn`, default empty so all existing payloads stay valid. Validate supported media type + base64 + a hard per-image byte cap. Images are deliberately **excluded** from the text digest/byte-budget invariants (`sanitized_input_text_digest`, `max_sanitized_input_bytes`) — those remain text-only.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gate5b4c3_shadow_generation_contract_image_blocks.py
import base64

import pytest

from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationImageBlock,
    Gate5B4C3ShadowGenerationTurn,
)

_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")


def _turn(**overrides):
    base = {
        "turnId": "turn_abc123",
        "turnDigest": "sha256:" + "a" * 64,
        "sanitizedCurrentTurnText": "describe this image",
        "sanitizedInputTextDigest": "sha256:" + "b" * 64,
    }
    base.update(overrides)
    return base


def test_turn_defaults_to_no_image_blocks():
    turn = Gate5B4C3ShadowGenerationTurn.model_validate(_turn())
    assert turn.sanitized_image_blocks == ()


def test_turn_round_trips_image_blocks():
    turn = Gate5B4C3ShadowGenerationTurn.model_validate(
        _turn(
            sanitizedImageBlocks=[
                {"mediaType": "image/png", "data": _PNG},
            ]
        )
    )
    assert len(turn.sanitized_image_blocks) == 1
    block = turn.sanitized_image_blocks[0]
    assert isinstance(block, Gate5B4C3ShadowGenerationImageBlock)
    assert block.media_type == "image/png"
    assert block.data == _PNG
    dumped = turn.model_dump(by_alias=True, mode="python")
    assert dumped["sanitizedImageBlocks"][0]["mediaType"] == "image/png"


def test_turn_rejects_unsupported_media_type():
    with pytest.raises(ValueError):
        Gate5B4C3ShadowGenerationTurn.model_validate(
            _turn(sanitizedImageBlocks=[{"mediaType": "image/svg+xml", "data": _PNG}])
        )


def test_turn_rejects_invalid_base64():
    with pytest.raises(ValueError):
        Gate5B4C3ShadowGenerationTurn.model_validate(
            _turn(sanitizedImageBlocks=[{"mediaType": "image/png", "data": "!!notbase64!!"}])
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MAGI_CONFIG=$(mktemp) uv run --extra dev pytest tests/test_gate5b4c3_shadow_generation_contract_image_blocks.py -q`
Expected: FAIL — `ImportError: cannot import name 'Gate5B4C3ShadowGenerationImageBlock'`

- [ ] **Step 3: Write minimal implementation**

In `gate5b4c3_shadow_generation_contract.py`, add the supported-types constant and the block model just before `class Gate5B4C3ShadowGenerationTurn` (~line 292). Reuse the existing `_Gate5B4C3Model` base (frozen, `extra="forbid"`, alias config) and `import base64`/`binascii` at top if not present.

```python
SUPPORTED_IMAGE_MEDIA_TYPES = frozenset(
    ("image/jpeg", "image/png", "image/gif", "image/webp")
)
MAX_USER_VISIBLE_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MiB per image, defense-in-depth


class Gate5B4C3ShadowGenerationImageBlock(_Gate5B4C3Model):
    media_type: str = Field(alias="mediaType")
    data: str = Field(alias="data")  # base64, no data-URL prefix

    @model_validator(mode="after")
    def _validate_block(self) -> "Gate5B4C3ShadowGenerationImageBlock":
        if self.media_type.lower() not in SUPPORTED_IMAGE_MEDIA_TYPES:
            raise ValueError("unsupported image media type")
        try:
            raw = base64.b64decode(self.data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("image data must be valid base64") from exc
        if not raw:
            raise ValueError("image data must be non-empty")
        if len(raw) > MAX_USER_VISIBLE_IMAGE_BYTES:
            raise ValueError("image exceeds per-image byte cap")
        return self
```

Then add the field to `Gate5B4C3ShadowGenerationTurn` (after `attachment_metadata`, ~line 303):

```python
    sanitized_image_blocks: tuple[Gate5B4C3ShadowGenerationImageBlock, ...] = Field(
        default=(),
        alias="sanitizedImageBlocks",
    )
```

Do **not** touch `_validate_turn`'s text digest/byte checks or the request-level digest invariant — images are intentionally outside the text-sanitizer digest.

- [ ] **Step 4: Run test to verify it passes**

Run: `MAGI_CONFIG=$(mktemp) uv run --extra dev pytest tests/test_gate5b4c3_shadow_generation_contract_image_blocks.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Run the existing contract suite for regression**

Run: `MAGI_CONFIG=$(mktemp) uv run --extra dev pytest -q -k gate5b4c3 tests/`
Expected: PASS (no regressions; new field is default-empty back-compat)

- [ ] **Step 6: Commit**

```bash
git add magi_agent/shadow/gate5b4c3_shadow_generation_contract.py tests/test_gate5b4c3_shadow_generation_contract_image_blocks.py
git commit -m "feat(gate5b4c3): carry sanitized image blocks on request turn"
```

---

## Task 3: Propagate image blocks into the runner input

**Files:**
- Modify: `magi_agent/shadow/gate5b4c3_runner_input_adapter.py` (`Gate5B4C3RunnerInput` ~100-147; `build_gate5b4c3_runner_input` ~291-319)
- Test: `tests/test_gate5b4c3_runner_input_adapter_image_blocks.py` (new)

Add a matching `sanitized_image_blocks` field to `Gate5B4C3RunnerInput` (reuse the contract's `Gate5B4C3ShadowGenerationImageBlock` type, default empty) and copy `request.turn.sanitized_image_blocks` into it inside `build_gate5b4c3_runner_input`. Images do not affect token/byte budgets.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gate5b4c3_runner_input_adapter_image_blocks.py
import base64

from magi_agent.shadow.gate5b4c3_runner_input_adapter import build_gate5b4c3_runner_input
from tests.support.gate5b4c3_factories import make_shadow_generation_request  # see note

_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")


def test_runner_input_carries_image_blocks():
    request = make_shadow_generation_request(
        sanitized_current_turn_text="describe this image",
        sanitized_image_blocks=[{"mediaType": "image/png", "data": _PNG}],
    )
    result = build_gate5b4c3_runner_input(request)
    assert result.status == "accepted"
    runner_input = result.runner_input_model()  # helper returns parsed Gate5B4C3RunnerInput
    assert len(runner_input.sanitized_image_blocks) == 1
    assert runner_input.sanitized_image_blocks[0].media_type == "image/png"


def test_runner_input_defaults_to_no_image_blocks():
    request = make_shadow_generation_request(sanitized_current_turn_text="hi")
    result = build_gate5b4c3_runner_input(request)
    assert result.status == "accepted"
    assert result.runner_input_model().sanitized_image_blocks == ()
```

> **Note for implementer:** A request factory may not already exist. If `tests/support/gate5b4c3_factories.py` is absent, first check existing gate5b4c3 adapter tests (`git grep -l "build_gate5b4c3_runner_input" tests/`) for how they construct a `Gate5B4C3ShadowGenerationRequest` and reuse/extract that builder into `tests/support/gate5b4c3_factories.py` with a `sanitized_image_blocks` kwarg that injects into `turn.sanitizedImageBlocks`. Similarly, if no `runner_input_model()` accessor exists on the adapter result, parse via `Gate5B4C3RunnerInput.model_validate(result.runner_input)` in the test instead. Keep the test asserting the same two behaviors.

- [ ] **Step 2: Run test to verify it fails**

Run: `MAGI_CONFIG=$(mktemp) uv run --extra dev pytest tests/test_gate5b4c3_runner_input_adapter_image_blocks.py -q`
Expected: FAIL — `AttributeError: 'Gate5B4C3RunnerInput' object has no attribute 'sanitized_image_blocks'`

- [ ] **Step 3: Write minimal implementation**

Add the import and field to `Gate5B4C3RunnerInput` (after `sanitized_recent_history`, ~line 111):

```python
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationImageBlock,
)
# ... inside Gate5B4C3RunnerInput:
    sanitized_image_blocks: tuple[Gate5B4C3ShadowGenerationImageBlock, ...] = Field(
        default=(),
        alias="sanitizedImageBlocks",
    )
```

In `build_gate5b4c3_runner_input`, pass it when constructing `Gate5B4C3RunnerInput` (add to the kwargs near line 296):

```python
        sanitizedImageBlocks=request.turn.sanitized_image_blocks,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MAGI_CONFIG=$(mktemp) uv run --extra dev pytest tests/test_gate5b4c3_runner_input_adapter_image_blocks.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add magi_agent/shadow/gate5b4c3_runner_input_adapter.py tests/test_gate5b4c3_runner_input_adapter_image_blocks.py tests/support/gate5b4c3_factories.py
git commit -m "feat(gate5b4c3): propagate image blocks into runner input"
```

---

## Task 4: Extract & sanitize image blocks at the HTTP layer

**Files:**
- Modify: `magi_agent/transport/chat.py` (`_extract_last_user_text` ~3788; `_build_user_visible_generation_request` ~3642-3716)
- Test: `tests/test_chat_image_extraction.py` (new)

Add `_extract_last_user_image_blocks(payload)` that finds the last user message and normalizes its content blocks into Anthropic-style blocks, then sanitizes via `message_builder._collect_image_blocks`. Handle two shapes:
- Native Anthropic: `{"type":"image","source":{"type":"base64","media_type","data"}}`
- OpenAI data-URL: `{"type":"image_url","image_url":{"url":"data:image/png;base64,<data>"}}`

Then wire the sanitized blocks into `turn_payload["sanitizedImageBlocks"]`, mapping each `{"source":{"media_type","data"}}` to `{"mediaType","data"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_image_extraction.py
import base64

from magi_agent.transport.chat import _extract_last_user_image_blocks

_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")


def test_extracts_native_anthropic_image_block():
    payload = {
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "what is this"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _PNG}},
            ]},
        ]
    }
    blocks = _extract_last_user_image_blocks(payload)
    assert len(blocks) == 1
    assert blocks[0]["source"]["media_type"] == "image/png"
    assert blocks[0]["source"]["data"] == _PNG


def test_extracts_openai_data_url_image_block():
    payload = {
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "what is this"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_PNG}"}},
            ]},
        ]
    }
    blocks = _extract_last_user_image_blocks(payload)
    assert len(blocks) == 1
    assert blocks[0]["source"]["media_type"] == "image/png"
    assert blocks[0]["source"]["data"] == _PNG


def test_drops_unsupported_and_text_only():
    payload = {
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "hi"},
                {"type": "image_url", "image_url": {"url": "data:image/svg+xml;base64," + _PNG}},
            ]},
        ]
    }
    assert _extract_last_user_image_blocks(payload) == []


def test_string_content_yields_no_images():
    payload = {"messages": [{"role": "user", "content": "plain text"}]}
    assert _extract_last_user_image_blocks(payload) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MAGI_CONFIG=$(mktemp) uv run --extra dev pytest tests/test_chat_image_extraction.py -q`
Expected: FAIL — `ImportError: cannot import name '_extract_last_user_image_blocks'`

- [ ] **Step 3: Write minimal implementation**

Add to `chat.py` (near `_extract_last_user_text`, ~3805). Reuse `_collect_image_blocks` for sanitization/caps.

```python
import re

from magi_agent.runtime.message_builder import _collect_image_blocks

_DATA_URL_RE = re.compile(r"^data:(?P<mime>[\w.+-]+/[\w.+-]+);base64,(?P<data>.+)$", re.DOTALL)


def _normalize_image_block(block: object) -> dict | None:
    if not isinstance(block, Mapping):
        return None
    block_type = block.get("type")
    if block_type == "image":
        return dict(block)  # already Anthropic-style; sanitized downstream
    if block_type == "image_url":
        image_url = block.get("image_url")
        url = image_url.get("url") if isinstance(image_url, Mapping) else None
        if not isinstance(url, str):
            return None
        match = _DATA_URL_RE.match(url.strip())
        if not match:
            return None
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": match.group("mime"),
                "data": match.group("data"),
            },
        }
    return None


def _extract_last_user_image_blocks(payload: Mapping[str, object]) -> list[dict]:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return []
    for message in reversed(messages):
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            return []
        normalized = [
            nb for nb in (_normalize_image_block(b) for b in content) if nb is not None
        ]
        # _collect_image_blocks reads the "imageBlocks" field + applies caps/validation
        return _collect_image_blocks({"imageBlocks": normalized}, {})
    return []
```

Then wire into `_build_user_visible_generation_request`. After `user_text = _extract_last_user_text(payload)` (~3654) add:

```python
    image_blocks = _extract_last_user_image_blocks(payload)
```

And in `turn_payload` (~3702), after `sanitizedCurrentTurnText`, add the mapped blocks:

```python
    if image_blocks:
        turn_payload["sanitizedImageBlocks"] = [
            {"mediaType": b["source"]["media_type"], "data": b["source"]["data"]}
            for b in image_blocks
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MAGI_CONFIG=$(mktemp) uv run --extra dev pytest tests/test_chat_image_extraction.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Run existing chat suite for regression**

Run: `MAGI_CONFIG=$(mktemp) uv run --extra dev pytest -q tests/ -k "chat or gate5b"`
Expected: PASS (text-only turns unchanged; `sanitizedImageBlocks` only added when present)

- [ ] **Step 6: Commit**

```bash
git add magi_agent/transport/chat.py tests/test_chat_image_extraction.py
git commit -m "feat(chat): extract and sanitize user image blocks into generation request"
```

---

## Task 5: Attach image parts to the opening ADK Content

**Files:**
- Modify: `magi_agent/shadow/gate5b4c3_live_runner_boundary.py` (opening `Content` build ~606-613)
- Test: `tests/test_gate5b4c3_live_runner_boundary_image_parts.py` (new)

At the opening `message = primitives.Content(...)` build, append one image part per `runner_input.sanitized_image_blocks` using the Task 1 converter with `primitives.Part.from_bytes` as the factory. The continuation `next_message` (~650/703) and finalizer (~1649) are unchanged — image goes on the opening turn only.

> **`from_bytes` fallback:** If `primitives.Part` lacks `from_bytes` in the installed `google-genai`, define a local factory `lambda *, data, mime_type: primitives.Part(inline_data=primitives.Blob(mime_type=mime_type, data=data))` and add `Blob=adk_runners.types.Blob` to `load_gate5b4c3_live_adk_primitives()` / `Gate5B4C3LiveAdkPrimitives`. The Task 1 RED test already pins the factory's `(data=bytes, mime_type=str)` signature, so only the factory binding changes.

Because constructing real ADK primitives in a unit test is heavy, this task tests a small extracted helper `_build_user_message_parts(runner_input, *, primitives)` that returns the parts list, using a fake `primitives` exposing `Part.from_text` and `Part.from_bytes`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gate5b4c3_live_runner_boundary_image_parts.py
import base64
from types import SimpleNamespace

from magi_agent.shadow.gate5b4c3_live_runner_boundary import _build_user_message_parts

_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")


def _fake_primitives():
    part = SimpleNamespace(
        from_text=lambda *, text: ("text", text),
        from_bytes=lambda *, data, mime_type: ("image", mime_type, data),
    )
    return SimpleNamespace(Part=part)


def test_text_only_input_yields_single_text_part():
    runner_input = SimpleNamespace(
        sanitized_user_input="hello",
        sanitized_recent_history=(),
        sanitized_image_blocks=(),
    )
    parts = _build_user_message_parts(runner_input, primitives=_fake_primitives())
    assert parts == [("text", "hello")]


def test_image_input_appends_image_parts_after_text():
    runner_input = SimpleNamespace(
        sanitized_user_input="describe this",
        sanitized_recent_history=(),
        sanitized_image_blocks=(
            SimpleNamespace(media_type="image/png", data=_PNG),
        ),
    )
    parts = _build_user_message_parts(runner_input, primitives=_fake_primitives())
    assert parts[0] == ("text", "describe this")
    assert parts[1] == ("image", "image/png", b"\x89PNG\r\n\x1a\n")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MAGI_CONFIG=$(mktemp) uv run --extra dev pytest tests/test_gate5b4c3_live_runner_boundary_image_parts.py -q`
Expected: FAIL — `ImportError: cannot import name '_build_user_message_parts'`

- [ ] **Step 3: Write minimal implementation**

In `gate5b4c3_live_runner_boundary.py`, add the helper near `_runner_message_text` (~921). The runner-input image blocks are `Gate5B4C3ShadowGenerationImageBlock` models (`.media_type`, `.data`); adapt them to the converter's expected `{"type":"image","source":{...}}` shape.

```python
from magi_agent.shadow.gate5b4c3_image_parts import image_blocks_to_parts


def _build_user_message_parts(runner_input: object, *, primitives: object) -> list:
    parts = [primitives.Part.from_text(text=_runner_message_text(runner_input))]
    raw_blocks = getattr(runner_input, "sanitized_image_blocks", ()) or ()
    converter_blocks = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": getattr(b, "media_type", None),
                "data": getattr(b, "data", None),
            },
        }
        for b in raw_blocks
    ]
    parts.extend(
        image_blocks_to_parts(converter_blocks, part_factory=primitives.Part.from_bytes)
    )
    return parts
```

Then replace the opening `message` build (~606-613) to use it:

```python
        try:
            message = primitives.Content(
                parts=_build_user_message_parts(runner_input, primitives=primitives),
                role="user",
            )
```

Leave the `next_message` continuation (~650/703) and finalizer (~1649) building exactly as they are (text-only).

- [ ] **Step 4: Run test to verify it passes**

Run: `MAGI_CONFIG=$(mktemp) uv run --extra dev pytest tests/test_gate5b4c3_live_runner_boundary_image_parts.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the live-runner-boundary suite for regression**

Run: `MAGI_CONFIG=$(mktemp) uv run --extra dev pytest -q tests/ -k "live_runner_boundary or gate5b4c3"`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add magi_agent/shadow/gate5b4c3_live_runner_boundary.py tests/test_gate5b4c3_live_runner_boundary_image_parts.py
git commit -m "feat(gate5b4c3): attach image parts to opening ADK content"
```

---

## Task 6: End-to-end stitch test + full focused regression

**Files:**
- Test: `tests/test_gate5b4c3_image_end_to_end.py` (new)

Verify the layers connect: a chat payload with an image → request (Task 4) → runner input (Task 3) → opening parts (Task 5, with fake primitives) contains an image part; and a text-only payload produces a single text part.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gate5b4c3_image_end_to_end.py
import base64
from types import SimpleNamespace

from magi_agent.shadow.gate5b4c3_live_runner_boundary import _build_user_message_parts
from magi_agent.shadow.gate5b4c3_runner_input_adapter import (
    Gate5B4C3RunnerInput,
    build_gate5b4c3_runner_input,
)
from tests.support.gate5b4c3_factories import make_shadow_generation_request

_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")


def _fake_primitives():
    part = SimpleNamespace(
        from_text=lambda *, text: ("text", text),
        from_bytes=lambda *, data, mime_type: ("image", mime_type, data),
    )
    return SimpleNamespace(Part=part)


def test_image_flows_request_to_opening_parts():
    request = make_shadow_generation_request(
        sanitized_current_turn_text="describe this image",
        sanitized_image_blocks=[{"mediaType": "image/png", "data": _PNG}],
    )
    result = build_gate5b4c3_runner_input(request)
    runner_input = Gate5B4C3RunnerInput.model_validate(result.runner_input)
    parts = _build_user_message_parts(runner_input, primitives=_fake_primitives())
    assert ("image", "image/png", b"\x89PNG\r\n\x1a\n") in parts


def test_text_only_request_yields_single_text_part():
    request = make_shadow_generation_request(sanitized_current_turn_text="just text")
    result = build_gate5b4c3_runner_input(request)
    runner_input = Gate5B4C3RunnerInput.model_validate(result.runner_input)
    parts = _build_user_message_parts(runner_input, primitives=_fake_primitives())
    assert parts == [("text", "just text")]
```

> Adjust `result.runner_input` access to match whatever Task 3 settled on (`result.runner_input` dict vs a model accessor). Keep the two assertions identical.

- [ ] **Step 2: Run test to verify it fails**

Run: `MAGI_CONFIG=$(mktemp) uv run --extra dev pytest tests/test_gate5b4c3_image_end_to_end.py -q`
Expected: FAIL (until Tasks 3–5 are merged; if run after them, write a sub-assertion first that doesn't yet hold). If all prior tasks are complete it should pass immediately — in that case this task is purely additive coverage.

- [ ] **Step 3: (No new implementation expected)**

This task adds coverage only. If it fails, the failure points to a real gap between layers — fix the specific layer, do not weaken the test.

- [ ] **Step 4: Run the full focused suite**

Run:
```bash
MAGI_CONFIG=$(mktemp) uv run --extra dev pytest -q \
  tests/test_gate5b4c3_image_parts.py \
  tests/test_gate5b4c3_shadow_generation_contract_image_blocks.py \
  tests/test_gate5b4c3_runner_input_adapter_image_blocks.py \
  tests/test_chat_image_extraction.py \
  tests/test_gate5b4c3_live_runner_boundary_image_parts.py \
  tests/test_gate5b4c3_image_end_to_end.py \
  tests/test_priority_a_message_builder.py
```
Expected: PASS (all)

- [ ] **Step 5: Broad regression across gate5b4c3 + chat**

Run: `MAGI_CONFIG=$(mktemp) uv run --extra dev pytest -q tests/ -k "gate5b4c3 or chat or message_builder"`
Expected: PASS (no regressions). Note: per repo conventions a couple of unrelated import-boundary tests may fail on this machine even on pristine main — confirm any failure pre-exists on `origin/main` before treating it as caused by this work.

- [ ] **Step 6: Commit**

```bash
git add tests/test_gate5b4c3_image_end_to_end.py
git commit -m "test(gate5b4c3): end-to-end image flow request -> opening content"
```

---

## Self-Review notes

- **Spec coverage:** L1 (Task 4), L2 (Task 2), L3 (Task 3), L4 (Tasks 1+5), converter+Blob-encoding decision (Task 1 + Task 5 fallback note), reuse of dead `message_builder` pipeline (Task 4), continuation/finalizer stay text (Task 5), drop-on-error (sanitization in Tasks 1/2/4), default-ON / no env gate (no flag introduced anywhere), video out of scope (nothing added). ✓
- **Type consistency:** `Gate5B4C3ShadowGenerationImageBlock` defined in Task 2, imported in Tasks 3 & 5. Converter factory signature `(*, data: bytes, mime_type: str)` is identical in Tasks 1, 5, 6. Field alias `sanitizedImageBlocks` consistent across contract, runner input, and chat turn payload. ✓
- **Known follow-ups (not in scope):** CLI/TUI path (`TurnControllerInput` / `RunnerSessionBoundary`) still text-only; image URL (non-data-URL) sources unsupported; multi-image relies on `message_builder` total-byte cap.
