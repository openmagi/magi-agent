# GAIA Benchmark Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reproducible, offline-testable GAIA benchmark harness to `magi_agent/benchmarks/gaia/` that scores the real model-backed CLI agent on the GAIA validation set.

**Architecture:** Thin layer over the already-landed real runner (`magi_agent/cli/real_runner.build_cli_model_runner`). Pure deterministic pieces (scorer, dataset loader, prompt/answer, Best-of-N) are unit-tested with no network. The agent loop is tested end-to-end with an injected fake `BaseLlm` (the existing `test_real_runner.py` pattern). Live scoring is env/key-gated and never runs in tests.

**Tech Stack:** Python 3.11, pydantic v2, pyarrow (parquet), google-adk (`LlmAgent`/`Runner`/`BaseLlm`), pytest. Live-only deps: `litellm` (providers extra), `HF_TOKEN` for dataset download, optional Composio for web tools.

**Scope this cycle:** dataset + scorer + prompt/answer + agent harness + Composio web tools (default-OFF) + Best-of-N + resumable runner/manifest. **Deferred follow-ups (NOT this cycle):** Task/Progress ledger orchestration, selective level-aware reflection gate, multi-role worker contracts. These are documented in `docs/plans/2026-06-05-gaia-bench-magi-agent-feasibility.md` §10 and tracked for a later PR.

**Conventions:** Mirror `magi_agent/benchmarks/coding_eval.py` (frozen pydantic models, `from __future__ import annotations`, no `any`). All new code under `magi_agent/benchmarks/gaia/`; tests under `tests/benchmarks/gaia/`. Run focused tests with `uv run --extra dev pytest tests/benchmarks/gaia -q`.

---

### Task 1: GAIA scorer (pure, official normalized exact-match)

**Files:**
- Create: `magi_agent/benchmarks/gaia/__init__.py` (empty)
- Create: `magi_agent/benchmarks/gaia/scorer.py`
- Test: `tests/benchmarks/gaia/__init__.py` (empty), `tests/benchmarks/gaia/test_scorer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/benchmarks/gaia/test_scorer.py
from __future__ import annotations

from magi_agent.benchmarks.gaia.scorer import question_scorer


def test_number_with_units_and_commas() -> None:
    assert question_scorer("17,000", "17000") is True
    assert question_scorer("$5.50", "5.5") is True
    assert question_scorer("18", "17") is False


def test_string_is_case_and_punct_insensitive() -> None:
    assert question_scorer("Egalitarian.", "egalitarian") is True
    assert question_scorer("FunkyMonkey", "funky monkey") is True


def test_comma_list_elementwise() -> None:
    assert question_scorer("apple, banana, pear", "apple,banana,pear") is True
    assert question_scorer("1, 2, 3", "1,2,3") is True
    assert question_scorer("apple, pear", "apple, banana, pear") is False


def test_list_numbers_compared_numerically() -> None:
    assert question_scorer("1,000; 2,000", "1000;2000") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/benchmarks/gaia/test_scorer.py -q`
Expected: FAIL (module `magi_agent.benchmarks.gaia.scorer` not found).

- [ ] **Step 3: Write minimal implementation**

```python
# magi_agent/benchmarks/gaia/scorer.py
"""Official GAIA answer scorer (normalized exact match).

Ported from the GAIA benchmark reference scorer so local scores match the
leaderboard's grading. Pure: no I/O, no model calls.
"""
from __future__ import annotations

import re
import string


def _is_float(element: object) -> bool:
    try:
        float(element)  # type: ignore[arg-type]
        return True
    except (ValueError, TypeError):
        return False


def normalize_number_str(number_str: str) -> float:
    for char in ("$", "%", ","):
        number_str = number_str.replace(char, "")
    try:
        return float(number_str)
    except ValueError:
        return float("inf")


def split_string(s: str, char_list: tuple[str, ...] = (",", ";")) -> list[str]:
    pattern = f"[{''.join(char_list)}]"
    return re.split(pattern, s)


def normalize_str(input_str: str, *, remove_punct: bool = True) -> str:
    no_spaces = re.sub(r"\s", "", input_str)
    if remove_punct:
        translator = str.maketrans("", "", string.punctuation)
        return no_spaces.lower().translate(translator)
    return no_spaces.lower()


def question_scorer(model_answer: str, ground_truth: str) -> bool:
    model_answer = "" if model_answer is None else str(model_answer)
    if _is_float(ground_truth):
        return normalize_number_str(model_answer) == float(ground_truth)
    if any(c in ground_truth for c in (",", ";")):
        gt_elems = split_string(ground_truth)
        ma_elems = split_string(model_answer)
        if len(gt_elems) != len(ma_elems):
            return False
        out: list[bool] = []
        for ma_elem, gt_elem in zip(ma_elems, gt_elems):
            if _is_float(gt_elem):
                out.append(normalize_number_str(ma_elem) == float(gt_elem))
            else:
                out.append(
                    normalize_str(ma_elem, remove_punct=False)
                    == normalize_str(gt_elem, remove_punct=False)
                )
        return all(out)
    return normalize_str(model_answer) == normalize_str(ground_truth)


__all__ = ["question_scorer", "normalize_number_str", "normalize_str", "split_string"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/benchmarks/gaia/test_scorer.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add magi_agent/benchmarks/gaia/__init__.py magi_agent/benchmarks/gaia/scorer.py tests/benchmarks/gaia/__init__.py tests/benchmarks/gaia/test_scorer.py
git commit -m "feat(gaia): official normalized exact-match scorer"
```

---

### Task 2: GAIA dataset model + parquet loader

**Files:**
- Create: `magi_agent/benchmarks/gaia/dataset.py`
- Test: `tests/benchmarks/gaia/test_dataset.py`

**Context:** GAIA validation metadata is a parquet at `2023/validation/metadata.parquet` with columns `task_id, Question, Level, Final answer, file_name, file_path, Annotator Metadata`. Attachments live at `2023/validation/<file_name>`. The loader reads a *local* parquet path (download is the runner's job, Task 7) and resolves attachment paths against a local dir. Tests build a tiny parquet in `tmp_path` with pyarrow — no network.

- [ ] **Step 1: Write the failing test**

```python
# tests/benchmarks/gaia/test_dataset.py
from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq

from magi_agent.benchmarks.gaia.dataset import load_gaia_questions


def _write_parquet(path, rows: list[dict]) -> None:
    cols = ["task_id", "Question", "Level", "Final answer", "file_name", "file_path", "Annotator Metadata"]
    table = pa.table({c: [r.get(c, "") for r in rows] for c in cols})
    pq.write_table(table, path)


def test_loads_rows_and_levels(tmp_path) -> None:
    p = tmp_path / "metadata.parquet"
    _write_parquet(p, [
        {"task_id": "a", "Question": "Q1", "Level": "1", "Final answer": "x", "file_name": ""},
        {"task_id": "b", "Question": "Q2", "Level": "2", "Final answer": "y", "file_name": "b.xlsx"},
    ])
    qs = load_gaia_questions(str(p), attachments_dir=str(tmp_path))
    assert [q.task_id for q in qs] == ["a", "b"]
    assert qs[0].level == 1 and qs[1].level == 2
    assert qs[0].attachment_path is None
    assert qs[1].attachment_path == str(tmp_path / "b.xlsx")


def test_level_filter(tmp_path) -> None:
    p = tmp_path / "metadata.parquet"
    _write_parquet(p, [
        {"task_id": "a", "Question": "Q1", "Level": "1", "Final answer": "x", "file_name": ""},
        {"task_id": "b", "Question": "Q2", "Level": "3", "Final answer": "y", "file_name": ""},
    ])
    qs = load_gaia_questions(str(p), attachments_dir=str(tmp_path), levels=(1,))
    assert [q.task_id for q in qs] == ["a"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/benchmarks/gaia/test_dataset.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Write minimal implementation**

```python
# magi_agent/benchmarks/gaia/dataset.py
"""Load GAIA questions from a local parquet metadata file."""
from __future__ import annotations

import os
from collections.abc import Sequence

import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class GaiaQuestion(BaseModel):
    model_config = _MODEL_CONFIG

    task_id: str
    question: str
    level: int
    final_answer: str = Field(default="")
    file_name: str = Field(default="")
    attachment_path: str | None = None


def load_gaia_questions(
    metadata_path: str,
    *,
    attachments_dir: str,
    levels: Sequence[int] | None = None,
) -> tuple[GaiaQuestion, ...]:
    table = pq.read_table(metadata_path)
    data = table.to_pydict()
    n = table.num_rows
    wanted = set(levels) if levels is not None else None
    out: list[GaiaQuestion] = []
    for i in range(n):
        level = int(str(data["Level"][i]))
        if wanted is not None and level not in wanted:
            continue
        file_name = str(data.get("file_name", [""] * n)[i] or "")
        attachment = os.path.join(attachments_dir, file_name) if file_name else None
        out.append(
            GaiaQuestion(
                task_id=str(data["task_id"][i]),
                question=str(data["Question"][i]),
                level=level,
                final_answer=str(data.get("Final answer", [""] * n)[i] or ""),
                file_name=file_name,
                attachment_path=attachment,
            )
        )
    return tuple(out)


__all__ = ["GaiaQuestion", "load_gaia_questions"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/benchmarks/gaia/test_dataset.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add magi_agent/benchmarks/gaia/dataset.py tests/benchmarks/gaia/test_dataset.py
git commit -m "feat(gaia): parquet dataset loader + GaiaQuestion model"
```

---

### Task 3: GAIA system prompt + FINAL ANSWER extraction

**Files:**
- Create: `magi_agent/benchmarks/gaia/answer.py`
- Test: `tests/benchmarks/gaia/test_answer.py`

**Context:** GAIA grades the text after `FINAL ANSWER:`. The system prompt instructs the agent to end with exactly that line; the extractor pulls the last occurrence (case-insensitive), strips surrounding whitespace and a trailing period.

- [ ] **Step 1: Write the failing test**

```python
# tests/benchmarks/gaia/test_answer.py
from __future__ import annotations

from magi_agent.benchmarks.gaia.answer import GAIA_SYSTEM_PROMPT, extract_final_answer


def test_extracts_last_final_answer() -> None:
    text = "thinking...\nFINAL ANSWER: 42\nnoise\nFINAL ANSWER: egalitarian"
    assert extract_final_answer(text) == "egalitarian"


def test_strips_trailing_period_and_space() -> None:
    assert extract_final_answer("FINAL ANSWER:  Paris . ") == "Paris"


def test_returns_empty_when_absent() -> None:
    assert extract_final_answer("no answer here") == ""


def test_prompt_mentions_final_answer_contract() -> None:
    assert "FINAL ANSWER" in GAIA_SYSTEM_PROMPT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/benchmarks/gaia/test_answer.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Write minimal implementation**

```python
# magi_agent/benchmarks/gaia/answer.py
"""GAIA system prompt and FINAL ANSWER extraction."""
from __future__ import annotations

import re

GAIA_SYSTEM_PROMPT = (
    "You are a general AI assistant solving a GAIA benchmark question. "
    "Use the available tools (web search/fetch, file reading, shell/python) to "
    "research and compute the answer. Report your reasoning, then finish with "
    "exactly one line:\n"
    "FINAL ANSWER: <answer>\n"
    "YOUR FINAL ANSWER should be a number OR as few words as possible OR a "
    "comma separated list of numbers and/or strings. If asked for a number, do "
    "not use commas or units (e.g. $ or %) unless specified. If asked for a "
    "string, do not use articles or abbreviations, and write digits in plain "
    "text unless specified. Apply these rules to each element of a list."
)

_FINAL_RE = re.compile(r"final answer\s*:\s*(.*)", re.IGNORECASE | re.DOTALL)


def extract_final_answer(text: str) -> str:
    matches = list(re.finditer(r"final answer\s*:", text, re.IGNORECASE))
    if not matches:
        return ""
    tail = text[matches[-1].end():]
    answer = tail.splitlines()[0] if tail.splitlines() else ""
    return answer.strip().rstrip(".").strip()


__all__ = ["GAIA_SYSTEM_PROMPT", "extract_final_answer"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/benchmarks/gaia/test_answer.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add magi_agent/benchmarks/gaia/answer.py tests/benchmarks/gaia/test_answer.py
git commit -m "feat(gaia): system prompt + FINAL ANSWER extraction"
```

---

### Task 4: GAIA agent harness over the real runner (fake-model end-to-end)

**Files:**
- Create: `magi_agent/benchmarks/gaia/harness.py`
- Test: `tests/benchmarks/gaia/test_harness.py`

**Context:** Reuse `magi_agent.cli.real_runner.build_cli_model_runner`. It already builds a real ADK `Agent`+`Runner` with core tools (FileRead/Bash/Glob/Grep — Bash gives us CodeAct/python) rooted at a workspace, and accepts `model_factory` (inject a fake `BaseLlm` in tests), `instruction`, `tools`, and `workspace_root`. `run_async(**kwargs)` yields ADK events; collect model text across events, then `extract_final_answer`. Seed the per-question workspace by copying the attachment (if any) into `workspace_root`. The fake-model pattern is in `magi_agent/cli/tests/test_real_runner.py` (`_FakeEchoLlm(BaseLlm)` overriding `generate_content_async`).

- [ ] **Step 1: Write the failing test**

```python
# tests/benchmarks/gaia/test_harness.py
from __future__ import annotations

from typing import AsyncGenerator

from google.adk.models import BaseLlm, LlmResponse
from google.genai import types

from magi_agent.benchmarks.gaia.dataset import GaiaQuestion
from magi_agent.benchmarks.gaia.harness import run_gaia_question


class _ScriptedLlm(BaseLlm):
    """Returns a fixed final answer (no provider traffic)."""

    async def generate_content_async(
        self, llm_request: object, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(text="reasoning...\nFINAL ANSWER: egalitarian")],
            )
        )


def test_run_extracts_final_answer_with_fake_model(tmp_path) -> None:
    q = GaiaQuestion(task_id="a", question="What word?", level=1, final_answer="egalitarian")
    answer = run_gaia_question(
        q,
        workspace_root=str(tmp_path),
        model_factory=lambda cfg: _ScriptedLlm(model="fake"),
    )
    assert answer == "egalitarian"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/benchmarks/gaia/test_harness.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Write minimal implementation**

Implement `run_gaia_question(question, *, workspace_root, model_factory=None, model="claude-opus-4-7", extra_tools=None, api_key="unused-in-tests")`:
1. If `question.attachment_path` exists, copy it into `workspace_root`.
2. Build `ProviderConfig(provider="anthropic", model=model, api_key=api_key)`.
3. `runner = build_cli_model_runner(config, instruction=GAIA_SYSTEM_PROMPT + "\n\n" + question.question, model_factory=model_factory, workspace_root=workspace_root, tools=extra_tools)` — note: when `extra_tools` is None, `build_cli_model_runner` wires the default CLI tools; to ADD web tools without losing defaults, Task 5 passes `build_cli_adk_tools(...) + web_tools`. For Task 4, leave `tools=None`.
4. Drive the runner to completion: build a user `types.Content` message, iterate `runner.run_async(user_id=..., session_id=..., new_message=...)`, collect model text parts.
5. Return `extract_final_answer("\n".join(collected_texts))`.

Use `asyncio.run` to drive the async generator. Mirror `_collect_text` from `test_real_runner.py`. Keep the function synchronous (returns `str`).

```python
# magi_agent/benchmarks/gaia/harness.py  (structure; implementer fills run loop)
from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Callable

from google.genai import types

from magi_agent.benchmarks.gaia.answer import GAIA_SYSTEM_PROMPT, extract_final_answer
from magi_agent.benchmarks.gaia.dataset import GaiaQuestion
from magi_agent.cli.providers import ProviderConfig
from magi_agent.cli.real_runner import build_cli_model_runner


def run_gaia_question(
    question: GaiaQuestion,
    *,
    workspace_root: str,
    model_factory: Callable[[ProviderConfig], object] | None = None,
    model: str = "claude-opus-4-7",
    extra_tools: list[object] | None = None,
    api_key: str = "unused-in-tests",
) -> str:
    if question.attachment_path and os.path.exists(question.attachment_path):
        shutil.copy(question.attachment_path, os.path.join(workspace_root, question.file_name))
    config = ProviderConfig(provider="anthropic", model=model, api_key=api_key)
    runner = build_cli_model_runner(
        config,
        instruction=f"{GAIA_SYSTEM_PROMPT}\n\nQUESTION:\n{question.question}",
        model_factory=model_factory,
        workspace_root=workspace_root,
        tools=extra_tools,
    )
    texts = asyncio.run(_drive(runner, question.question))
    return extract_final_answer("\n".join(texts))


async def _drive(runner: object, question: str) -> list[str]:
    message = types.Content(role="user", parts=[types.Part(text=question)])
    texts: list[str] = []
    async for event in runner.run_async(  # type: ignore[attr-defined]
        user_id="gaia", session_id="gaia", new_message=message
    ):
        content = getattr(event, "content", None)
        for part in getattr(content, "parts", None) or []:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text:
                texts.append(text)
    return texts


__all__ = ["run_gaia_question"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/benchmarks/gaia/test_harness.py -q`
Expected: PASS. If the runner requires `invocation_id`, pass a fixed one. Adjust kwargs to match `CliModelRunner.run_async` (`user_id/session_id/new_message`).

- [ ] **Step 5: Commit**

```bash
git add magi_agent/benchmarks/gaia/harness.py tests/benchmarks/gaia/test_harness.py
git commit -m "feat(gaia): agent harness over real runner with fake-model e2e test"
```

---

### Task 5: Composio web tools (default-OFF, env/key-gated)

**Files:**
- Create: `magi_agent/benchmarks/gaia/web_tools.py`
- Test: `tests/benchmarks/gaia/test_web_tools.py`

**Context:** GAIA needs live web for ~76% of tasks. Reuse `magi_agent.composio.config.ComposioConfig` + `magi_agent.composio.mcp.build_composio_toolset_bundle` to produce an ADK `McpToolset` from env (`MAGI_COMPOSIO_ENABLED`, `COMPOSIO_API_KEY`, `MAGI_COMPOSIO_TOOLKITS`). Return `[]` when disabled so the harness stays runnable offline. Tests inject a fake client factory (no network) — see `magi_agent/cli/tests/test_composio_cli.py` and `magi_agent/composio/mcp.py` `build_composio_toolset_bundle(..., composio_client_factory=...)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/benchmarks/gaia/test_web_tools.py
from __future__ import annotations

from magi_agent.benchmarks.gaia.web_tools import build_web_tools


def test_disabled_returns_empty(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_COMPOSIO_ENABLED", raising=False)
    assert build_web_tools(env={}) == []


def test_enabled_without_key_returns_empty(monkeypatch) -> None:
    assert build_web_tools(env={"MAGI_COMPOSIO_ENABLED": "1"}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/benchmarks/gaia/test_web_tools.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Write minimal implementation**

`build_web_tools(env=os.environ)` returns `list[object]`:
1. Build `ComposioConfig.from_env(env)` (use the existing constructor — read `magi_agent/composio/config.py` for the exact factory name).
2. If not active → return `[]`.
3. Else `bundle = build_composio_toolset_bundle(config)`; return `list(bundle.toolsets)` when `bundle.active` else `[]`.
Keep all failures soft (return `[]`) so the offline harness never raises.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/benchmarks/gaia/test_web_tools.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add magi_agent/benchmarks/gaia/web_tools.py tests/benchmarks/gaia/test_web_tools.py
git commit -m "feat(gaia): default-off Composio web tools for the harness"
```

---

### Task 6: Best-of-N answer selection (budget-gated, deterministic vote)

**Files:**
- Create: `magi_agent/benchmarks/gaia/best_of_n.py`
- Test: `tests/benchmarks/gaia/test_best_of_n.py`

**Context:** OAgents found Best-of-N the most reliable test-time lever. Run the harness `n` times, normalize each answer with the scorer's `normalize_str`, take the majority vote (ties broken by first occurrence → deterministic). `n` is a budget knob (default 1 = no extra cost).

- [ ] **Step 1: Write the failing test**

```python
# tests/benchmarks/gaia/test_best_of_n.py
from __future__ import annotations

from magi_agent.benchmarks.gaia.best_of_n import majority_vote


def test_majority_picks_most_common() -> None:
    assert majority_vote(["Paris", "paris.", "London"]) == "Paris"


def test_tie_breaks_by_first_occurrence() -> None:
    assert majority_vote(["b", "a", "a", "b"]) == "b"


def test_empty_returns_empty() -> None:
    assert majority_vote([]) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/benchmarks/gaia/test_best_of_n.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Write minimal implementation**

```python
# magi_agent/benchmarks/gaia/best_of_n.py
"""Deterministic Best-of-N answer selection by normalized majority vote."""
from __future__ import annotations

from collections.abc import Sequence

from magi_agent.benchmarks.gaia.scorer import normalize_str


def majority_vote(answers: Sequence[str]) -> str:
    counts: dict[str, int] = {}
    rep: dict[str, str] = {}
    order: list[str] = []
    for a in answers:
        key = normalize_str(a)
        if key not in counts:
            counts[key] = 0
            rep[key] = a
            order.append(key)
        counts[key] += 1
    if not order:
        return ""
    best = max(order, key=lambda k: counts[k])
    return rep[best]


__all__ = ["majority_vote"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/benchmarks/gaia/test_best_of_n.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add magi_agent/benchmarks/gaia/best_of_n.py tests/benchmarks/gaia/test_best_of_n.py
git commit -m "feat(gaia): deterministic Best-of-N majority vote"
```

---

### Task 7: Resumable runner + manifest + per-level report (fake-model smoke)

**Files:**
- Create: `magi_agent/benchmarks/gaia/run.py`
- Create: `magi_agent/benchmarks/gaia/download.py` (HF parquet + attachment fetch; live-only, not exercised in tests)
- Test: `tests/benchmarks/gaia/test_run.py`

**Context:** Tie it together. `run_benchmark(questions, *, output_dir, runner_fn, n=1, model=...)` iterates questions, runs `runner_fn` (defaults to `run_gaia_question`; tests inject a stub) up to `n` times with `majority_vote`, scores with `question_scorer`, appends one JSONL record per question to `output_dir/results.jsonl`, writes `output_dir/manifest.json` (model, n, dataset_path, counts), and is **resumable**: on start it reads existing `results.jsonl` and skips already-scored `task_id`s. Returns a per-level accuracy dict. `download.py` resolves the parquet + attachments from HF using `HF_TOKEN` (URL pattern `https://huggingface.co/datasets/gaia-benchmark/GAIA/resolve/main/2023/validation/<path>`); keep it import-light and untested (live-only).

- [ ] **Step 1: Write the failing test**

```python
# tests/benchmarks/gaia/test_run.py
from __future__ import annotations

import json

from magi_agent.benchmarks.gaia.dataset import GaiaQuestion
from magi_agent.benchmarks.gaia.run import run_benchmark


def _questions() -> list[GaiaQuestion]:
    return [
        GaiaQuestion(task_id="a", question="Q1", level=1, final_answer="paris"),
        GaiaQuestion(task_id="b", question="Q2", level=2, final_answer="42"),
    ]


def test_scores_and_writes_results(tmp_path) -> None:
    answers = {"a": "Paris", "b": "41"}  # a correct, b wrong
    report = run_benchmark(
        _questions(),
        output_dir=str(tmp_path),
        runner_fn=lambda q, **kw: answers[q.task_id],
    )
    assert report["per_level"]["1"]["correct"] == 1
    assert report["per_level"]["2"]["correct"] == 0
    lines = (tmp_path / "results.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["task_id"] == "a"
    assert (tmp_path / "manifest.json").exists()


def test_resume_skips_scored(tmp_path) -> None:
    answers = {"a": "Paris", "b": "42"}
    run_benchmark(_questions()[:1], output_dir=str(tmp_path),
                  runner_fn=lambda q, **kw: answers[q.task_id])
    calls: list[str] = []

    def _runner(q, **kw):
        calls.append(q.task_id)
        return answers[q.task_id]

    run_benchmark(_questions(), output_dir=str(tmp_path), runner_fn=_runner)
    assert calls == ["b"]  # "a" already scored, skipped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/benchmarks/gaia/test_run.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Write minimal implementation**

Implement `run_benchmark` per the context above. Record schema per line: `{"task_id", "level", "answer", "ground_truth", "correct"}`. `manifest.json`: `{"model", "n", "count", "output_dir"}`. Per-level report: `{"per_level": {level: {"correct", "total"}}, "overall": {"correct", "total"}}`. For `n>1`, call `runner_fn` n times and `majority_vote`. Use `question_scorer(answer, q.final_answer)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/benchmarks/gaia/test_run.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add magi_agent/benchmarks/gaia/run.py magi_agent/benchmarks/gaia/download.py tests/benchmarks/gaia/test_run.py
git commit -m "feat(gaia): resumable runner, manifest, per-level report"
```

---

## Final verification (after all tasks)

- [ ] Run the whole GAIA suite: `uv run --extra dev pytest tests/benchmarks/gaia -q` → all pass.
- [ ] Run the full repo suite to check no regressions: `uv run --extra dev pytest -q`.
- [ ] Confirm no live network/model calls happen in any test (fake models + stubs only).
- [ ] Update `docs/superpowers/plans/2026-06-05-gaia-bench-harness.md` deferred-items note if scope changed.

## Live run (manual, out of scope for this PR's tests)

Documented for the follow-up that actually scores:
1. `uv sync --extra dev --extra cli --extra providers` (adds `litellm`).
2. `export ANTHROPIC_API_KEY=... HF_TOKEN=... MAGI_MODEL=claude-opus-4-7`.
3. Optional web: `export MAGI_COMPOSIO_ENABLED=1 COMPOSIO_API_KEY=... MAGI_COMPOSIO_TOOLKITS=...`.
4. Download dataset via `download.py`, then `run_benchmark(load_gaia_questions(...), runner_fn=run_gaia_question)`.
