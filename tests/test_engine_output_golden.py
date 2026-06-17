"""Golden matrix for MagiEngineDriver.run_turn_stream output (Phase 0 Task 0.3).

Locks the observable event stream + terminal across three representative
scenarios.  Goldens are written on first run and compared on subsequent runs.
Set ``UPDATE_GOLDEN=1`` to refresh all goldens after an intentional behaviour
change.

Scenarios
---------
text_only
    Runner emits two text-delta events.  Exercises the pure streaming text path.

tool_then_final
    Runner emits a text delta, a function-call event, a function-response event,
    then a final text delta.  Exercises the tool roundtrip path.

function_call_only
    Runner emits only a single function-call event with no following text.
    Exercises the no-final-text path.

Volatile fields
---------------
``engine_capture._VOLATILE_KEYS`` replaces all timing/epoch fields with the
sentinel ``"<normalized>"`` before comparison.  No additional normalization is
required here because the engine_capture module already covers all volatile
keys emitted by ``MagiEngineDriver``.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from tests.support.engine_capture import capture_engine_turn
from tests.support.engine_fakes import MockRunner, call_event, response_event, text_event

_GOLDEN_DIR = Path(__file__).parent / "golden" / "engine"


def _scenarios() -> dict[str, tuple[dict, MockRunner]]:
    ti = lambda sid: {"prompt": "go", "session_id": sid, "turn_id": "t1"}
    return {
        "text_only": (
            ti("s-text"),
            MockRunner([text_event("hello "), text_event("world")]),
        ),
        "tool_then_final": (
            ti("s-tool"),
            MockRunner(
                [
                    text_event("working "),
                    call_event("Bash", {"cmd": "ls"}, "c1"),
                    response_event("Bash", {"out": "file.txt"}, "c1"),
                    text_event("done"),
                ]
            ),
        ),
        "function_call_only": (
            ti("s-call"),
            MockRunner([call_event("Bash", {"cmd": "ls"}, "c1")]),
        ),
    }


@pytest.mark.parametrize("name", sorted(_scenarios()))
def test_engine_output_matches_golden(name: str) -> None:
    """Compare engine output to a stored golden snapshot.

    On first run (or when UPDATE_GOLDEN=1) the golden is written; on
    subsequent runs the serialised snapshot must match byte-for-byte.
    """
    turn_input, runner = _scenarios()[name]
    snap = asyncio.run(capture_engine_turn(turn_input, runner))
    blob = json.dumps(snap, indent=2, sort_keys=True, default=str) + "\n"
    golden = _GOLDEN_DIR / f"{name}.json"
    if not golden.exists() or os.environ.get("UPDATE_GOLDEN") == "1":
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(blob, encoding="utf-8")
        return
    assert blob == golden.read_text(encoding="utf-8"), (
        f"engine golden drift for {name!r}; "
        "re-run with UPDATE_GOLDEN=1 if the change is intentional.\n"
        f"Golden path: {golden}"
    )
