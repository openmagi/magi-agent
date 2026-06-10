"""Gated live Gemini vision check for the CLI image-attach path (Task 5).

Skipped unless a Google/Gemini API key is present in the environment, so this
test is CI-safe. When a key IS set, it drives the REAL ``MagiEngineDriver``
(constructed exactly as ``headless.py`` / ``build_headless_runtime`` does) with
a base64-encoded solid-red PNG and asserts that the model's response mentions
"red".

PNG generation uses stdlib ``zlib`` + ``struct`` only — no Pillow dependency.

Engine construction
-------------------
``build_headless_runtime(session_id=...)`` is the canonical factory used by
``run_headless`` (see ``headless.py`` line 645: ``active_driver = driver if
driver is not None else MagiEngineDriver()`` where the driver wraps the runner
built by this same factory). We call it here to get a real ``MagiEngineDriver``
backed by a Gemini ``CliModelRunner``.

Stream collection
-----------------
``MagiEngineDriver.run_turn_stream`` is an async generator that yields
``RuntimeEvent`` objects and then a terminal ``EngineResult`` as its final item
(per the consumption convention in ``cli.contracts``). Text from the model
arrives as ``RuntimeEvent(type="token", payload={"type": "text_delta",
"delta": "<chunk>"})`` for the real ADK engine (the ``delta`` key), mirroring
the ``_token_text`` / ``_accumulate_text`` helpers in ``headless.py`` and the
``("token", "text_delta") in kinds`` assertion in
``magi_agent/cli/tests/test_engine.py``.  We drain the generator using the
same ``drain()`` helper that ``run_headless`` itself uses.
"""

from __future__ import annotations

import asyncio
import base64
import os
import struct
import zlib

import pytest

_KEY_ENVS = ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENAI_API_KEY")


def _red_png() -> bytes:
    """Return the bytes of a tiny (8x8) solid-red PNG via stdlib only."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    w = h = 8
    # filter byte (0 = None) + RGB red (0xff 0x00 0x00) per pixel, per row
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


@pytest.mark.skipif(
    not any(os.environ.get(k) for k in _KEY_ENVS),
    reason="no Google/Gemini API key set; live vision check skipped",
)
def test_real_gemini_sees_red_image() -> None:
    """Drive the real Gemini model through the CLI engine and check it sees red.

    Uses ``asyncio.run`` (the sync-test convention from
    ``magi_agent/cli/tests/test_engine.py``) rather than ``pytest.mark.asyncio``
    to avoid any asyncio-mode configuration dependency.
    """

    from magi_agent.cli.contracts import EngineResult, TurnInput
    from magi_agent.cli.headless import _accumulate_text, drain
    from magi_agent.cli.wiring import build_headless_runtime

    block: dict[str, object] = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(_red_png()).decode("ascii"),
        },
    }
    turn = TurnInput(
        prompt="What single color fills this image? Answer with just the color name.",
        image_blocks=(block,),
    )

    async def _run() -> str:
        # Mirror headless.py / run_headless exactly:
        #   active_driver = driver if driver is not None else MagiEngineDriver()
        # build_headless_runtime() is what constructs that MagiEngineDriver with
        # a real runner when a provider key is present — the same factory used
        # by the production headless path.
        session_id = "test-live-vision"
        rt = build_headless_runtime(session_id=session_id)
        engine = rt.engine

        cancel = asyncio.Event()
        gen = engine.run_turn_stream(None, turn, cancel=cancel, gate=rt.gate)
        events, _terminal = await drain(gen)
        return _accumulate_text(events)

    text = asyncio.run(_run())
    assert "red" in text.lower(), f"Expected 'red' in model reply, got: {text!r}"
