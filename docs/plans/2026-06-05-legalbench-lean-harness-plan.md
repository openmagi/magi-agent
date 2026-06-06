# LegalBench Lean Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a lean, evidence-backed legal recipe pack for `magi-agent` whose four score-moving levers are individually toggleable composable-determinism checkpoints, plus a pure post-hoc scorer that reports per-checkpoint LegalBench lift over a Claude baseline.

**Architecture:** Pure, self-contained modules for each lever (few-shot selection, rule injection, prompt-variant selection, constrained output parsing) composed by a thin recipe with per-checkpoint toggles. A runner takes an injected `complete(prompt) -> str` model callable (real provider in prod, fake in tests) and emits answer records. A pure `legal_eval` scorer (cloned from the `coding_eval` post-hoc pattern) consumes records and produces balanced-accuracy + lift + ablation reports. Default-OFF gate.

**Tech Stack:** Python 3.11, pydantic v2 (frozen models, as in the repo), `uv` for env/test, pytest. LegalBench data read from local TSV files (`train.tsv`/`test.tsv`) + `base_prompt.txt` per task (official `HazyResearch/legalbench` layout) — no network.

**Reference files to mirror (read before starting):**
- `magi_agent/benchmarks/coding_eval.py` — pure post-hoc evaluator pattern (schema version, scoring categories, frozen pydantic models, `model_validator`). Clone its style.
- `magi_agent/recipes/first_party/coding/` — first-party recipe package layout.
- Design: `docs/plans/2026-06-05-legalbench-lean-harness-design.md`.

**Conventions:**
- All data models are frozen pydantic `BaseModel` with `ConfigDict(frozen=True)`, matching `coding_eval.py`.
- `from __future__ import annotations` at top of every file.
- Tests colocated under `tests/benchmarks/legalbench/` and `tests/recipes/first_party/legal/`.
- Run tests with: `uv run --extra dev pytest <path> -v`.
- Commit after every task.

**Shared type vocabulary (defined in Task 1, used everywhere):**
- `ReasoningType = Literal["issue", "rule-recall", "rule-application", "rule-conclusion", "interpretation", "rhetorical"]`
- `Example` — one labeled instance: `fields: Mapping[str, str]`, `answer: str`.
- `LegalTask` — `task_id: str`, `reasoning_type: ReasoningType`, `base_prompt: str` (a `str.format`-style template referencing `{field}` names), `train: tuple[Example, ...]`, `test: tuple[Example, ...]`, `labels: tuple[str, ...]` (canonical answer set).

---

## Task 0: Baseline — confirm clean test suite

**Files:** none (verification only)

- [ ] **Step 1: Confirm the worktree builds and the focused suite is green**

Run: `cd /Users/kevin/Desktop/claude_code/magi-agent-oss-worktrees/legalbench-harness && uv run --extra dev pytest magi_agent/benchmarks -q`
Expected: PASS (existing `coding_eval` tests, if any) or "no tests ran" — either is an acceptable clean baseline. If failures appear, STOP and report.

---

## Task 1: Core data models + task loader

**Files:**
- Create: `magi_agent/benchmarks/legalbench/__init__.py` (empty)
- Create: `magi_agent/benchmarks/legalbench/models.py`
- Create: `magi_agent/benchmarks/legalbench/loader.py`
- Test: `tests/benchmarks/legalbench/test_loader.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/benchmarks/legalbench/test_loader.py
from __future__ import annotations

from pathlib import Path

from magi_agent.benchmarks.legalbench.loader import load_task
from magi_agent.benchmarks.legalbench.models import LegalTask


def _write_task(root: Path) -> Path:
    task = root / "abercrombie"
    task.mkdir(parents=True)
    (task / "base_prompt.txt").write_text(
        "Mark: {text}\nIs it generic? Answer Yes or No.\nAnswer:"
    )
    (task / "train.tsv").write_text(
        "text\tanswer\n" "soft soap for soap\tYes\n" "STAR for cars\tNo\n"
    )
    (task / "test.tsv").write_text("text\tanswer\n" "ivory for ivory\tYes\n")
    return task


def test_load_task_reads_splits_prompt_and_labels(tmp_path: Path) -> None:
    task_dir = _write_task(tmp_path)
    task = load_task(task_dir, reasoning_type="rule-conclusion")
    assert isinstance(task, LegalTask)
    assert task.task_id == "abercrombie"
    assert task.reasoning_type == "rule-conclusion"
    assert "{text}" in task.base_prompt
    assert len(task.train) == 2
    assert task.train[0].fields["text"] == "soft soap for soap"
    assert task.train[0].answer == "Yes"
    assert len(task.test) == 1
    assert task.labels == ("No", "Yes")  # sorted unique answers from train
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/benchmarks/legalbench/test_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: magi_agent.benchmarks.legalbench.models`

- [ ] **Step 3: Write `models.py`**

```python
# magi_agent/benchmarks/legalbench/models.py
from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict

ReasoningType = Literal[
    "issue",
    "rule-recall",
    "rule-application",
    "rule-conclusion",
    "interpretation",
    "rhetorical",
]

_FROZEN = ConfigDict(frozen=True)


class Example(BaseModel):
    model_config = _FROZEN
    fields: Mapping[str, str]
    answer: str


class LegalTask(BaseModel):
    model_config = _FROZEN
    task_id: str
    reasoning_type: ReasoningType
    base_prompt: str
    train: tuple[Example, ...]
    test: tuple[Example, ...]
    labels: tuple[str, ...]
```

- [ ] **Step 4: Write `loader.py`**

```python
# magi_agent/benchmarks/legalbench/loader.py
from __future__ import annotations

import csv
from pathlib import Path

from magi_agent.benchmarks.legalbench.models import Example, LegalTask, ReasoningType


def _read_tsv(path: Path) -> tuple[Example, ...]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = list(reader)
    examples: list[Example] = []
    for row in rows:
        answer = (row.get("answer") or "").strip()
        fields = {k: v for k, v in row.items() if k != "answer"}
        examples.append(Example(fields=fields, answer=answer))
    return tuple(examples)


def load_task(task_dir: Path, *, reasoning_type: ReasoningType) -> LegalTask:
    base_prompt = (task_dir / "base_prompt.txt").read_text(encoding="utf-8")
    train = _read_tsv(task_dir / "train.tsv")
    test = _read_tsv(task_dir / "test.tsv")
    labels = tuple(sorted({ex.answer for ex in train if ex.answer}))
    return LegalTask(
        task_id=task_dir.name,
        reasoning_type=reasoning_type,
        base_prompt=base_prompt,
        train=train,
        test=test,
        labels=labels,
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/benchmarks/legalbench/test_loader.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add magi_agent/benchmarks/legalbench/ tests/benchmarks/legalbench/test_loader.py
git commit -m "feat(legalbench): core data models and task loader"
```

---

## Task 2: Curated subset manifest loader

**Files:**
- Create: `magi_agent/benchmarks/legalbench/manifest.py`
- Test: `tests/benchmarks/legalbench/test_manifest.py`

The manifest is the curated v1 task list (task_id → reasoning_type). It lives as
a JSON file so curation is data, not code.

- [ ] **Step 1: Write the failing test**

```python
# tests/benchmarks/legalbench/test_manifest.py
from __future__ import annotations

import json
from pathlib import Path

from magi_agent.benchmarks.legalbench.manifest import load_subset


def _scaffold(root: Path) -> tuple[Path, Path]:
    data = root / "data"
    for name, ans in (("abercrombie", "Yes"), ("hearsay", "No")):
        d = data / name
        d.mkdir(parents=True)
        (d / "base_prompt.txt").write_text("{text}\nAnswer:")
        (d / "train.tsv").write_text(f"text\tanswer\nx\t{ans}\n")
        (d / "test.tsv").write_text(f"text\tanswer\ny\t{ans}\n")
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {"task_id": "abercrombie", "reasoning_type": "rule-conclusion"},
                {"task_id": "hearsay", "reasoning_type": "rule-application"},
            ]
        )
    )
    return data, manifest


def test_load_subset_returns_tasks_in_manifest_order(tmp_path: Path) -> None:
    data, manifest = _scaffold(tmp_path)
    tasks = load_subset(data_root=data, manifest_path=manifest)
    assert [t.task_id for t in tasks] == ["abercrombie", "hearsay"]
    assert tasks[1].reasoning_type == "rule-application"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/benchmarks/legalbench/test_manifest.py -v`
Expected: FAIL — `ModuleNotFoundError: ...manifest`

- [ ] **Step 3: Write `manifest.py`**

```python
# magi_agent/benchmarks/legalbench/manifest.py
from __future__ import annotations

import json
from pathlib import Path

from magi_agent.benchmarks.legalbench.loader import load_task
from magi_agent.benchmarks.legalbench.models import LegalTask


def load_subset(*, data_root: Path, manifest_path: Path) -> tuple[LegalTask, ...]:
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    tasks: list[LegalTask] = []
    for entry in entries:
        task_dir = data_root / entry["task_id"]
        tasks.append(load_task(task_dir, reasoning_type=entry["reasoning_type"]))
    return tuple(tasks)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/benchmarks/legalbench/test_manifest.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add magi_agent/benchmarks/legalbench/manifest.py tests/benchmarks/legalbench/test_manifest.py
git commit -m "feat(legalbench): curated subset manifest loader"
```

---

## Task 3: Checkpoint 1 — curated few-shot selector

**Files:**
- Create: `magi_agent/recipes/first_party/legal/__init__.py` (empty)
- Create: `magi_agent/recipes/first_party/legal/fewshot.py`
- Test: `tests/recipes/first_party/legal/test_fewshot.py`

Curation = caller may pin exact exemplar indices per task (`curated_indices`).
When not pinned, selection is a deterministic seeded sample (NOT random across
runs). Either way the result is reproducible.

- [ ] **Step 1: Write the failing test**

```python
# tests/recipes/first_party/legal/test_fewshot.py
from __future__ import annotations

from magi_agent.benchmarks.legalbench.models import Example, LegalTask
from magi_agent.recipes.first_party.legal.fewshot import select_fewshot


def _task() -> LegalTask:
    train = tuple(
        Example(fields={"text": f"ex{i}"}, answer="Yes" if i % 2 else "No")
        for i in range(5)
    )
    return LegalTask(
        task_id="t",
        reasoning_type="rule-conclusion",
        base_prompt="{text}\nAnswer:",
        train=train,
        test=(),
        labels=("No", "Yes"),
    )


def test_curated_indices_are_honored_in_order() -> None:
    chosen = select_fewshot(_task(), k=2, seed=0, curated_indices=(3, 1))
    assert [e.fields["text"] for e in chosen] == ["ex3", "ex1"]


def test_seeded_selection_is_deterministic() -> None:
    a = select_fewshot(_task(), k=3, seed=7)
    b = select_fewshot(_task(), k=3, seed=7)
    assert [e.fields["text"] for e in a] == [e.fields["text"] for e in b]
    assert len(a) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/recipes/first_party/legal/test_fewshot.py -v`
Expected: FAIL — `ModuleNotFoundError: ...legal.fewshot`

- [ ] **Step 3: Write `fewshot.py`**

```python
# magi_agent/recipes/first_party/legal/fewshot.py
from __future__ import annotations

import random

from magi_agent.benchmarks.legalbench.models import Example, LegalTask


def select_fewshot(
    task: LegalTask,
    *,
    k: int,
    seed: int,
    curated_indices: tuple[int, ...] | None = None,
) -> tuple[Example, ...]:
    if curated_indices is not None:
        return tuple(task.train[i] for i in curated_indices)
    if k >= len(task.train):
        return task.train
    rng = random.Random(seed)
    idx = sorted(rng.sample(range(len(task.train)), k))
    return tuple(task.train[i] for i in idx)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/recipes/first_party/legal/test_fewshot.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add magi_agent/recipes/first_party/legal/__init__.py magi_agent/recipes/first_party/legal/fewshot.py tests/recipes/first_party/legal/test_fewshot.py
git commit -m "feat(legal): curated deterministic few-shot selector checkpoint"
```

---

## Task 4: Checkpoint 2 — explicit rule-statement injection

**Files:**
- Create: `magi_agent/recipes/first_party/legal/rule_inject.py`
- Test: `tests/recipes/first_party/legal/test_rule_inject.py`

A per-task library maps `task_id` → an explicit statement of the legal rule the
task tests. Injection prepends it; tasks with no rule entry are passed through
unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/recipes/first_party/legal/test_rule_inject.py
from __future__ import annotations

from magi_agent.recipes.first_party.legal.rule_inject import (
    RULE_STATEMENTS,
    inject_rule,
)


def test_known_task_prepends_rule_statement() -> None:
    out = inject_rule("PROMPT BODY", task_id="abercrombie")
    assert out.startswith(RULE_STATEMENTS["abercrombie"])
    assert out.endswith("PROMPT BODY")
    assert "\n\n" in out


def test_unknown_task_is_passed_through_unchanged() -> None:
    assert inject_rule("PROMPT BODY", task_id="no_such_task") == "PROMPT BODY"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/recipes/first_party/legal/test_rule_inject.py -v`
Expected: FAIL — `ModuleNotFoundError: ...rule_inject`

- [ ] **Step 3: Write `rule_inject.py`**

```python
# magi_agent/recipes/first_party/legal/rule_inject.py
from __future__ import annotations

# Explicit statements of the legal rule each task tests. These rules are rare in
# pretraining, so stating them is the highest-reliability LegalBench lever for
# rule-conclusion / rule-application tasks. Extend per curated task.
RULE_STATEMENTS: dict[str, str] = {
    "abercrombie": (
        "Rule: Trademark distinctiveness falls on the Abercrombie spectrum: "
        "generic (never protectable), descriptive (protectable only with "
        "secondary meaning), suggestive, arbitrary, or fanciful (inherently "
        "distinctive). A mark is generic when it names the product category "
        "itself."
    ),
    "hearsay": (
        "Rule: Hearsay is an out-of-court statement offered to prove the truth "
        "of the matter asserted. A statement offered for a non-truth purpose "
        "(e.g., effect on the listener, notice, or a verbal act) is not hearsay."
    ),
}


def inject_rule(prompt_body: str, *, task_id: str) -> str:
    rule = RULE_STATEMENTS.get(task_id)
    if rule is None:
        return prompt_body
    return f"{rule}\n\n{prompt_body}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/recipes/first_party/legal/test_rule_inject.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add magi_agent/recipes/first_party/legal/rule_inject.py tests/recipes/first_party/legal/test_rule_inject.py
git commit -m "feat(legal): explicit rule-statement injection checkpoint"
```

---

## Task 5: Checkpoint 3 — per-task prompt-variant selection

**Files:**
- Create: `magi_agent/recipes/first_party/legal/prompt_variants.py`
- Test: `tests/recipes/first_party/legal/test_prompt_variants.py`

Variant choice (`plain` vs `technical`) is decided on the train split offline and
frozen in `PROMPT_VARIANTS`. `select_variant` returns the frozen choice
(default `plain`). `phrase_instruction` returns the instruction style applied to
the task's base prompt.

- [ ] **Step 1: Write the failing test**

```python
# tests/recipes/first_party/legal/test_prompt_variants.py
from __future__ import annotations

from magi_agent.recipes.first_party.legal.prompt_variants import (
    PROMPT_VARIANTS,
    phrase_instruction,
    select_variant,
)


def test_frozen_choice_is_returned_for_known_task() -> None:
    PROMPT_VARIANTS["abercrombie"] = "technical"
    assert select_variant("abercrombie") == "technical"


def test_unknown_task_defaults_to_plain() -> None:
    assert select_variant("no_such_task") == "plain"


def test_phrase_instruction_differs_by_variant() -> None:
    plain = phrase_instruction("Decide the answer.", variant="plain")
    technical = phrase_instruction("Decide the answer.", variant="technical")
    assert plain != technical
    assert "Decide the answer." in plain
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/recipes/first_party/legal/test_prompt_variants.py -v`
Expected: FAIL — `ModuleNotFoundError: ...prompt_variants`

- [ ] **Step 3: Write `prompt_variants.py`**

```python
# magi_agent/recipes/first_party/legal/prompt_variants.py
from __future__ import annotations

from typing import Literal

Variant = Literal["plain", "technical"]

# Per-task variant chosen on the TRAIN split and frozen here. Do not tune on test.
PROMPT_VARIANTS: dict[str, Variant] = {}


def select_variant(task_id: str) -> Variant:
    return PROMPT_VARIANTS.get(task_id, "plain")


def phrase_instruction(instruction: str, *, variant: Variant) -> str:
    if variant == "technical":
        return f"As a legal expert applying the controlling rule: {instruction}"
    return f"Read carefully and answer. {instruction}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/recipes/first_party/legal/test_prompt_variants.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add magi_agent/recipes/first_party/legal/prompt_variants.py tests/recipes/first_party/legal/test_prompt_variants.py
git commit -m "feat(legal): per-task prompt-variant selection checkpoint"
```

---

## Task 6: Checkpoint 4 — constrained output parser

**Files:**
- Create: `magi_agent/recipes/first_party/legal/output_parser.py`
- Test: `tests/recipes/first_party/legal/test_output_parser.py`

Maps free-form model text to a canonical label from `labels`. Matching is
case-insensitive and tolerant of surrounding prose; returns `None` when no label
is found (so the scorer can mark it `partial`/`fail`).

- [ ] **Step 1: Write the failing test**

```python
# tests/recipes/first_party/legal/test_output_parser.py
from __future__ import annotations

from magi_agent.recipes.first_party.legal.output_parser import parse_answer


def test_exact_label_after_prose() -> None:
    assert parse_answer("The answer is: Yes.", labels=("Yes", "No")) == "Yes"


def test_case_insensitive_match() -> None:
    assert parse_answer("no", labels=("Yes", "No")) == "No"


def test_prefers_first_label_token_when_both_present() -> None:
    # Model echoes options then concludes — take the last standalone label.
    assert parse_answer("Yes or No? No", labels=("Yes", "No")) == "No"


def test_no_label_returns_none() -> None:
    assert parse_answer("I am not sure.", labels=("Yes", "No")) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/recipes/first_party/legal/test_output_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: ...output_parser`

- [ ] **Step 3: Write `output_parser.py`**

```python
# magi_agent/recipes/first_party/legal/output_parser.py
from __future__ import annotations

import re


def parse_answer(raw: str, *, labels: tuple[str, ...]) -> str | None:
    text = raw.strip()
    # Scan from the end: the model's final standalone label token is the answer.
    best: tuple[int, str] | None = None
    for label in labels:
        for match in re.finditer(
            rf"(?<![\w]){re.escape(label)}(?![\w])", text, flags=re.IGNORECASE
        ):
            pos = match.start()
            if best is None or pos > best[0]:
                best = (pos, label)
    return best[1] if best else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/recipes/first_party/legal/test_output_parser.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add magi_agent/recipes/first_party/legal/output_parser.py tests/recipes/first_party/legal/test_output_parser.py
git commit -m "feat(legal): constrained output parser checkpoint"
```

---

## Task 7: Recipe — compose checkpoints with toggles

**Files:**
- Create: `magi_agent/recipes/first_party/legal/recipe.py`
- Test: `tests/recipes/first_party/legal/test_recipe.py`

`LegalCheckpoints` is the toggle set. `build_prompt` applies enabled checkpoints
in order: render base prompt with the test instance fields → instruction variant
→ few-shot block → rule injection. `parse_output` applies constrained parsing
when enabled, else returns the trimmed raw text.

- [ ] **Step 1: Write the failing test**

```python
# tests/recipes/first_party/legal/test_recipe.py
from __future__ import annotations

from magi_agent.benchmarks.legalbench.models import Example, LegalTask
from magi_agent.recipes.first_party.legal.recipe import (
    LegalCheckpoints,
    build_prompt,
    parse_output,
)


def _task() -> LegalTask:
    train = (
        Example(fields={"text": "soft soap"}, answer="Yes"),
        Example(fields={"text": "STAR cars"}, answer="No"),
    )
    test = (Example(fields={"text": "ivory soap"}, answer="Yes"),)
    return LegalTask(
        task_id="abercrombie",
        reasoning_type="rule-conclusion",
        base_prompt="Mark: {text}\nAnswer:",
        train=train,
        test=test,
        labels=("No", "Yes"),
    )


def test_all_checkpoints_off_renders_only_base_prompt() -> None:
    cp = LegalCheckpoints(
        few_shot=False, rule_inject=False, prompt_variant=False, constrained_parse=False
    )
    prompt = build_prompt(_task(), _task().test[0], checkpoints=cp)
    assert prompt == "Mark: ivory soap\nAnswer:"


def test_checkpoints_on_inject_rule_and_fewshot() -> None:
    cp = LegalCheckpoints(few_shot=True, rule_inject=True, k=2, seed=0)
    prompt = build_prompt(_task(), _task().test[0], checkpoints=cp)
    assert prompt.startswith("Rule: Trademark")  # rule injected first
    assert "soft soap" in prompt  # few-shot exemplar present
    assert "Mark: ivory soap" in prompt  # test instance present


def test_parse_output_respects_toggle() -> None:
    cp_on = LegalCheckpoints(constrained_parse=True)
    cp_off = LegalCheckpoints(constrained_parse=False)
    assert parse_output("answer: No", _task(), checkpoints=cp_on) == "No"
    assert parse_output("answer: No", _task(), checkpoints=cp_off) == "answer: No"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/recipes/first_party/legal/test_recipe.py -v`
Expected: FAIL — `ModuleNotFoundError: ...legal.recipe`

- [ ] **Step 3: Write `recipe.py`**

```python
# magi_agent/recipes/first_party/legal/recipe.py
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from magi_agent.benchmarks.legalbench.models import Example, LegalTask
from magi_agent.recipes.first_party.legal.fewshot import select_fewshot
from magi_agent.recipes.first_party.legal.output_parser import parse_answer
from magi_agent.recipes.first_party.legal.prompt_variants import (
    phrase_instruction,
    select_variant,
)
from magi_agent.recipes.first_party.legal.rule_inject import inject_rule


class LegalCheckpoints(BaseModel):
    model_config = ConfigDict(frozen=True)
    few_shot: bool = True
    rule_inject: bool = True
    prompt_variant: bool = True
    constrained_parse: bool = True
    k: int = 4
    seed: int = 0


def _render(base_prompt: str, example: Example) -> str:
    return base_prompt.format(**example.fields)


def _fewshot_block(task: LegalTask, checkpoints: LegalCheckpoints) -> str:
    shots = select_fewshot(task, k=checkpoints.k, seed=checkpoints.seed)
    rendered = [f"{_render(task.base_prompt, ex)} {ex.answer}" for ex in shots]
    return "\n\n".join(rendered)


def build_prompt(
    task: LegalTask, example: Example, *, checkpoints: LegalCheckpoints
) -> str:
    body = _render(task.base_prompt, example)
    if checkpoints.prompt_variant:
        variant = select_variant(task.task_id)
        body = phrase_instruction(body, variant=variant)
    if checkpoints.few_shot:
        body = f"{_fewshot_block(task, checkpoints)}\n\n{body}"
    if checkpoints.rule_inject:
        body = inject_rule(body, task_id=task.task_id)
    return body


def parse_output(
    raw: str, task: LegalTask, *, checkpoints: LegalCheckpoints
) -> str | None:
    if not checkpoints.constrained_parse:
        return raw.strip()
    return parse_answer(raw, labels=task.labels)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/recipes/first_party/legal/test_recipe.py -v`
Expected: PASS

Note: `test_all_checkpoints_off_renders_only_base_prompt` requires
`prompt_variant=False`; the default is `True`, so the test sets it `False`
explicitly — confirm the assertion matches the no-variant base render.

- [ ] **Step 5: Commit**

```bash
git add magi_agent/recipes/first_party/legal/recipe.py tests/recipes/first_party/legal/test_recipe.py
git commit -m "feat(legal): recipe composing toggleable determinism checkpoints"
```

---

## Task 8: Answer records + pure scorer (`legal_eval`)

**Files:**
- Create: `magi_agent/benchmarks/legal_eval.py`
- Test: `tests/benchmarks/test_legal_eval.py`

Pure, post-hoc — no model calls (mirrors `coding_eval.py`). Consumes
`AnswerRecord`s and produces a `LegalReport`: per-reasoning-type balanced
accuracy + overall, plus a `lift` helper comparing two reports.

Balanced accuracy for a task = mean over gold classes of (correct-in-class /
total-in-class). Per reasoning type = mean of its tasks' balanced accuracy.

- [ ] **Step 1: Write the failing test**

```python
# tests/benchmarks/test_legal_eval.py
from __future__ import annotations

from magi_agent.benchmarks.legal_eval import (
    AnswerRecord,
    lift,
    score,
)


def _records(pred_map: dict[str, str]) -> list[AnswerRecord]:
    # Two-class task; gold alternates Yes/No.
    gold = ["Yes", "No", "Yes", "No"]
    return [
        AnswerRecord(
            task_id="abercrombie",
            reasoning_type="rule-conclusion",
            index=i,
            predicted=pred_map.get(str(i)),
            gold=gold[i],
        )
        for i in range(4)
    ]


def test_perfect_predictions_score_one() -> None:
    recs = _records({"0": "Yes", "1": "No", "2": "Yes", "3": "No"})
    report = score(recs)
    assert report.overall_balanced_accuracy == 1.0
    assert report.by_reasoning_type["rule-conclusion"] == 1.0


def test_balanced_accuracy_handles_class_imbalance() -> None:
    # Predict everything "Yes": Yes-recall=1.0, No-recall=0.0 -> balanced 0.5
    recs = _records({"0": "Yes", "1": "Yes", "2": "Yes", "3": "Yes"})
    report = score(recs)
    assert report.overall_balanced_accuracy == 0.5


def test_lift_is_harness_minus_baseline() -> None:
    harness = score(_records({"0": "Yes", "1": "No", "2": "Yes", "3": "No"}))
    baseline = score(_records({"0": "Yes", "1": "Yes", "2": "Yes", "3": "Yes"}))
    assert lift(harness=harness, baseline=baseline).overall == 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/benchmarks/test_legal_eval.py -v`
Expected: FAIL — `ModuleNotFoundError: ...legal_eval`

- [ ] **Step 3: Write `legal_eval.py`**

```python
# magi_agent/benchmarks/legal_eval.py
"""LegalBench post-hoc evaluator. No provider/model calls are made here; it
scores recorded answer records against gold labels (mirrors coding_eval.py)."""
from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel, ConfigDict

from magi_agent.benchmarks.legalbench.models import ReasoningType

LEGAL_BENCHMARK_SCHEMA_VERSION = "legalBenchTasks.v1"

_FROZEN = ConfigDict(frozen=True)


class AnswerRecord(BaseModel):
    model_config = _FROZEN
    task_id: str
    reasoning_type: ReasoningType
    index: int
    predicted: str | None
    gold: str


class LegalReport(BaseModel):
    model_config = _FROZEN
    schema_version: str = LEGAL_BENCHMARK_SCHEMA_VERSION
    overall_balanced_accuracy: float
    by_reasoning_type: dict[str, float]
    by_task: dict[str, float]


class LegalLift(BaseModel):
    model_config = _FROZEN
    overall: float
    by_reasoning_type: dict[str, float]


def _balanced_accuracy(pairs: list[tuple[str | None, str]]) -> float:
    by_class_total: dict[str, int] = defaultdict(int)
    by_class_correct: dict[str, int] = defaultdict(int)
    for predicted, gold in pairs:
        by_class_total[gold] += 1
        if predicted == gold:
            by_class_correct[gold] += 1
    if not by_class_total:
        return 0.0
    recalls = [by_class_correct[c] / by_class_total[c] for c in by_class_total]
    return sum(recalls) / len(recalls)


def score(records: list[AnswerRecord]) -> LegalReport:
    by_task_pairs: dict[str, list[tuple[str | None, str]]] = defaultdict(list)
    task_reasoning: dict[str, ReasoningType] = {}
    for rec in records:
        by_task_pairs[rec.task_id].append((rec.predicted, rec.gold))
        task_reasoning[rec.task_id] = rec.reasoning_type

    by_task = {tid: _balanced_accuracy(pairs) for tid, pairs in by_task_pairs.items()}

    rt_scores: dict[str, list[float]] = defaultdict(list)
    for tid, acc in by_task.items():
        rt_scores[task_reasoning[tid]].append(acc)
    by_reasoning_type = {rt: sum(v) / len(v) for rt, v in rt_scores.items()}

    overall = (
        sum(by_reasoning_type.values()) / len(by_reasoning_type)
        if by_reasoning_type
        else 0.0
    )
    return LegalReport(
        overall_balanced_accuracy=overall,
        by_reasoning_type=by_reasoning_type,
        by_task=by_task,
    )


def lift(*, harness: LegalReport, baseline: LegalReport) -> LegalLift:
    rt = {
        key: harness.by_reasoning_type.get(key, 0.0) - baseline.by_reasoning_type.get(key, 0.0)
        for key in harness.by_reasoning_type
    }
    return LegalLift(
        overall=harness.overall_balanced_accuracy - baseline.overall_balanced_accuracy,
        by_reasoning_type=rt,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/benchmarks/test_legal_eval.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add magi_agent/benchmarks/legal_eval.py tests/benchmarks/test_legal_eval.py
git commit -m "feat(legalbench): pure post-hoc balanced-accuracy scorer + lift"
```

---

## Task 9: Runner — model boundary, baseline/harness/ablation modes

**Files:**
- Create: `magi_agent/benchmarks/legalbench/runner.py`
- Test: `tests/benchmarks/legalbench/test_runner.py`

The runner takes an injected `complete: Callable[[str], str]` (real provider in
prod; fake in tests). It builds prompts via the recipe, calls `complete`, parses,
and returns `AnswerRecord`s. `run_subset` runs every test instance for a list of
tasks under a given `LegalCheckpoints`. Baseline = all checkpoints off. Ablation
= run once per single-checkpoint-disabled config.

- [ ] **Step 1: Write the failing test**

```python
# tests/benchmarks/legalbench/test_runner.py
from __future__ import annotations

from magi_agent.benchmarks.legalbench.models import Example, LegalTask
from magi_agent.benchmarks.legalbench.runner import run_subset
from magi_agent.recipes.first_party.legal.recipe import LegalCheckpoints


def _task() -> LegalTask:
    return LegalTask(
        task_id="abercrombie",
        reasoning_type="rule-conclusion",
        base_prompt="Mark: {text}\nAnswer:",
        train=(Example(fields={"text": "soft soap"}, answer="Yes"),),
        test=(
            Example(fields={"text": "ivory"}, answer="Yes"),
            Example(fields={"text": "STAR"}, answer="No"),
        ),
        labels=("No", "Yes"),
    )


def test_run_subset_produces_one_record_per_test_instance() -> None:
    def fake_complete(prompt: str) -> str:
        return "Yes" if "ivory" in prompt else "No"

    records = run_subset(
        [_task()], complete=fake_complete, checkpoints=LegalCheckpoints()
    )
    assert len(records) == 2
    assert records[0].predicted == "Yes"
    assert records[0].gold == "Yes"
    assert records[1].predicted == "No"
    assert records[0].reasoning_type == "rule-conclusion"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/benchmarks/legalbench/test_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: ...runner`

- [ ] **Step 3: Write `runner.py`**

```python
# magi_agent/benchmarks/legalbench/runner.py
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from magi_agent.benchmarks.legal_eval import AnswerRecord
from magi_agent.benchmarks.legalbench.models import LegalTask
from magi_agent.recipes.first_party.legal.recipe import (
    LegalCheckpoints,
    build_prompt,
    parse_output,
)

Complete = Callable[[str], str]


def run_subset(
    tasks: Sequence[LegalTask],
    *,
    complete: Complete,
    checkpoints: LegalCheckpoints,
) -> list[AnswerRecord]:
    records: list[AnswerRecord] = []
    for task in tasks:
        for index, example in enumerate(task.test):
            prompt = build_prompt(task, example, checkpoints=checkpoints)
            raw = complete(prompt)
            predicted = parse_output(raw, task, checkpoints=checkpoints)
            records.append(
                AnswerRecord(
                    task_id=task.task_id,
                    reasoning_type=task.reasoning_type,
                    index=index,
                    predicted=predicted,
                    gold=example.answer,
                )
            )
    return records


def baseline_checkpoints() -> LegalCheckpoints:
    return LegalCheckpoints(
        few_shot=False,
        rule_inject=False,
        prompt_variant=False,
        constrained_parse=False,
    )


@dataclass(frozen=True)
class AblationCell:
    disabled: str
    checkpoints: LegalCheckpoints


def ablation_configs(full: LegalCheckpoints) -> list[AblationCell]:
    cells: list[AblationCell] = []
    for field in ("few_shot", "rule_inject", "prompt_variant", "constrained_parse"):
        cells.append(AblationCell(disabled=field, checkpoints=replace_flag(full, field)))
    return cells


def replace_flag(checkpoints: LegalCheckpoints, field: str) -> LegalCheckpoints:
    return checkpoints.model_copy(update={field: False})
```

Note: `LegalCheckpoints` is a frozen pydantic model, so toggling a flag uses
`model_copy(update={field: False})` (the `replace_flag` helper), NOT
`dataclasses.replace`. `@dataclass` is used only for the plain `AblationCell`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/benchmarks/legalbench/test_runner.py -v`
Expected: PASS

- [ ] **Step 5: Add an ablation test**

```python
# append to tests/benchmarks/legalbench/test_runner.py
from magi_agent.benchmarks.legalbench.runner import (
    ablation_configs,
    baseline_checkpoints,
)
from magi_agent.recipes.first_party.legal.recipe import LegalCheckpoints


def test_baseline_disables_all_checkpoints() -> None:
    cp = baseline_checkpoints()
    assert not (cp.few_shot or cp.rule_inject or cp.prompt_variant or cp.constrained_parse)


def test_ablation_yields_one_config_per_checkpoint() -> None:
    cells = ablation_configs(LegalCheckpoints())
    assert {c.disabled for c in cells} == {
        "few_shot",
        "rule_inject",
        "prompt_variant",
        "constrained_parse",
    }
    fs = next(c for c in cells if c.disabled == "few_shot")
    assert fs.checkpoints.few_shot is False
    assert fs.checkpoints.rule_inject is True
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run --extra dev pytest tests/benchmarks/legalbench/test_runner.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add magi_agent/benchmarks/legalbench/runner.py tests/benchmarks/legalbench/test_runner.py
git commit -m "feat(legalbench): runner with injected model boundary + ablation/baseline modes"
```

---

## Task 10: Default-OFF gate + provider binding + CLI entry

**Files:**
- Create: `magi_agent/benchmarks/legalbench/cli.py`
- Modify: register entry (read `magi_agent/__main__.py` and `magi_agent/cli/` to
  find the subcommand-registration pattern; follow it exactly — do NOT invent a
  new dispatch mechanism).
- Test: `tests/benchmarks/legalbench/test_cli.py`

The gate: the CLI entry raises/exits unless `MAGI_LEGAL_HARNESS_ENABLED=1`. The
provider binding (`_real_complete`) is the single place that calls the live model
— implement it against the repo's existing provider/model client (find it by
reading how `runtime/adk_turn_runner.py` obtains a model; reuse that client for a
plain single-turn completion). Tests never hit the network: they exercise the
gate and the run wiring with a fake `complete`.

- [ ] **Step 1: Write the failing test (gate behavior)**

```python
# tests/benchmarks/legalbench/test_cli.py
from __future__ import annotations

import pytest

from magi_agent.benchmarks.legalbench.cli import GateDisabledError, ensure_enabled


def test_gate_blocks_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_LEGAL_HARNESS_ENABLED", raising=False)
    with pytest.raises(GateDisabledError):
        ensure_enabled()


def test_gate_allows_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_LEGAL_HARNESS_ENABLED", "1")
    ensure_enabled()  # does not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/benchmarks/legalbench/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: ...cli`

- [ ] **Step 3: Write the gate + budget-guarded run in `cli.py`**

```python
# magi_agent/benchmarks/legalbench/cli.py
from __future__ import annotations

import os
from pathlib import Path

from magi_agent.benchmarks.legal_eval import LegalReport, score
from magi_agent.benchmarks.legalbench.manifest import load_subset
from magi_agent.benchmarks.legalbench.runner import (
    Complete,
    baseline_checkpoints,
    run_subset,
)
from magi_agent.recipes.first_party.legal.recipe import LegalCheckpoints

_GATE_ENV = "MAGI_LEGAL_HARNESS_ENABLED"


class GateDisabledError(RuntimeError):
    pass


def ensure_enabled() -> None:
    if os.environ.get(_GATE_ENV) != "1":
        raise GateDisabledError(
            f"Legal harness is gated off. Set {_GATE_ENV}=1 to run."
        )


def run_eval(
    *,
    data_root: Path,
    manifest_path: Path,
    complete: Complete,
    max_tasks: int | None = None,
) -> tuple[LegalReport, LegalReport]:
    ensure_enabled()
    tasks = load_subset(data_root=data_root, manifest_path=manifest_path)
    if max_tasks is not None:
        tasks = tasks[:max_tasks]
    harness = score(
        run_subset(tasks, complete=complete, checkpoints=LegalCheckpoints())
    )
    baseline = score(
        run_subset(tasks, complete=complete, checkpoints=baseline_checkpoints())
    )
    return harness, baseline
```

- [ ] **Step 4: Run the gate test to verify it passes**

Run: `uv run --extra dev pytest tests/benchmarks/legalbench/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Add a run_eval wiring test with a fake provider**

```python
# append to tests/benchmarks/legalbench/test_cli.py
import json
from pathlib import Path

from magi_agent.benchmarks.legalbench.cli import run_eval


def test_run_eval_returns_harness_and_baseline_reports(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("MAGI_LEGAL_HARNESS_ENABLED", "1")
    data = tmp_path / "data" / "abercrombie"
    data.mkdir(parents=True)
    (data / "base_prompt.txt").write_text("Mark: {text}\nAnswer:")
    (data / "train.tsv").write_text("text\tanswer\nsoft\tYes\nstar\tNo\n")
    (data / "test.tsv").write_text("text\tanswer\nivory\tYes\n")
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps([{"task_id": "abercrombie", "reasoning_type": "rule-conclusion"}])
    )

    harness, baseline = run_eval(
        data_root=tmp_path / "data",
        manifest_path=manifest,
        complete=lambda prompt: "Yes",
    )
    assert harness.overall_balanced_accuracy == 1.0
    assert baseline.overall_balanced_accuracy == 1.0
```

- [ ] **Step 6: Run all legalbench tests**

Run: `uv run --extra dev pytest tests/benchmarks/legalbench tests/benchmarks/test_legal_eval.py tests/recipes/first_party/legal -v`
Expected: PASS (all)

- [ ] **Step 7: Wire the provider + CLI subcommand**

Read `magi_agent/__main__.py` and `magi_agent/cli/` to find the existing
subcommand registration. Add a `legalbench` subcommand that: calls
`ensure_enabled()`, builds `_real_complete` from the repo's model client (mirror
how `runtime/adk_turn_runner.py` constructs/calls the model — single-turn, no
tools), calls `run_eval(...)`, and prints `harness`, `baseline`, and `lift(...)`
as JSON. Add `--max-tasks`, `--data-root`, `--manifest` flags. Do not add a new
dispatch framework; follow the existing one.

- [ ] **Step 8: Manual smoke (gated, optional — needs provider creds)**

Run: `MAGI_LEGAL_HARNESS_ENABLED=1 uv run magi legalbench --data-root data/legalbench --manifest data/legalbench/manifest.v1.json --max-tasks 2`
Expected: prints harness/baseline/lift JSON. If no creds, skip and note it.

- [ ] **Step 9: Commit**

```bash
git add magi_agent/benchmarks/legalbench/cli.py tests/benchmarks/legalbench/test_cli.py magi_agent/__main__.py
git commit -m "feat(legalbench): default-OFF gate, provider binding, CLI entry"
```

---

## Task 11: Curated v1 data manifest + docs

**Files:**
- Create: `data/legalbench/manifest.v1.json` (curated ~20–40 tasks across all 6
  reasoning types — start with the tasks that have `RULE_STATEMENTS` entries and
  expand)
- Create: `data/legalbench/README.md` (how to populate per-task `train.tsv`,
  `test.tsv`, `base_prompt.txt` from `HazyResearch/legalbench`; license note)
- Modify: `magi_agent/recipes/first_party/legal/rule_inject.py` (add rule
  statements for each curated rule-based task)
- Modify: `magi_agent/recipes/first_party/legal/prompt_variants.py` (freeze the
  train-split-chosen variant per task)

- [ ] **Step 1: Write `manifest.v1.json`** — JSON array of
  `{"task_id", "reasoning_type"}` covering all 6 reasoning types. Example seed:

```json
[
  {"task_id": "abercrombie", "reasoning_type": "rule-conclusion"},
  {"task_id": "hearsay", "reasoning_type": "rule-application"},
  {"task_id": "definition_classification", "reasoning_type": "interpretation"},
  {"task_id": "rule_qa", "reasoning_type": "rule-recall"},
  {"task_id": "issue_spotting", "reasoning_type": "issue"},
  {"task_id": "canada_tax_court_outcomes", "reasoning_type": "rhetorical"}
]
```

- [ ] **Step 2: Write `data/legalbench/README.md`** documenting the per-task file
  layout, the source repo, and that data files are NOT committed (add
  `data/legalbench/*/` to `.gitignore` if the repo policy excludes datasets;
  check `.gitignore` first and follow repo convention).

- [ ] **Step 3: Methodology note** — append a short section to
  `docs/plans/2026-06-05-legalbench-lean-harness-design.md` recording which
  variant/few-shot indices were chosen per task on the train split (the frozen
  decisions), so the numbers are reproducible.

- [ ] **Step 4: Run the full focused suite**

Run: `uv run --extra dev pytest tests/benchmarks tests/recipes/first_party/legal -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add data/legalbench/manifest.v1.json data/legalbench/README.md magi_agent/recipes/first_party/legal/rule_inject.py magi_agent/recipes/first_party/legal/prompt_variants.py docs/plans/2026-06-05-legalbench-lean-harness-design.md
git commit -m "feat(legalbench): curated v1 manifest, rule library, frozen variants, methodology"
```

---

## Final verification

- [ ] Run the whole new suite green:
  `uv run --extra dev pytest tests/benchmarks tests/recipes/first_party/legal -v`
- [ ] Lint/type per repo norm (find the command in `pyproject.toml`; e.g.
  `uv run --extra dev ruff check magi_agent/benchmarks/legalbench magi_agent/recipes/first_party/legal` and the repo's type checker).
- [ ] Confirm gate default-OFF: with `MAGI_LEGAL_HARNESS_ENABLED` unset, the CLI
  refuses to run.
- [ ] Confirm no network is required by any test.
```
