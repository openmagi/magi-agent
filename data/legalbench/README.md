# data/legalbench

This directory holds the curated task manifest and (when populated) per-task
dataset files for the LegalBench lean harness.

## Per-task file layout

Each task lives in its own subdirectory named after the `task_id`:

```
data/legalbench/
  manifest.v1.json          ← curated task list (tracked)
  README.md                 ← this file (tracked)
  <task_id>/                ← per-task data (NOT committed — see .gitignore)
    base_prompt.txt         ← prompt template with {field} placeholders
    train.tsv               ← tab-separated: field columns + "answer" column
    test.tsv                ← same layout as train.tsv
```

`base_prompt.txt` is a Python `str.format`-style template.  Every column name
in the TSV (except `answer`) is a valid placeholder.  Example:

```
Mark: {text}
Is the mark generic under the Abercrombie spectrum?
Answer Yes or No.
Answer:
```

`train.tsv` and `test.tsv` are tab-separated with a header row.  The
`answer` column holds the canonical label; all other columns are instance
fields.

## Source

Dataset files come from the official LegalBench repository:

- GitHub: https://github.com/HazyResearch/legalbench
- HuggingFace: https://huggingface.co/datasets/nguha/legalbench

Each task in the repository ships exactly the three files above.  Download
the tasks listed in `manifest.v1.json` and place them under this directory.

### Quick download (single task)

```bash
# Example: abercrombie task
TASK=abercrombie
mkdir -p data/legalbench/$TASK
BASE=https://raw.githubusercontent.com/HazyResearch/legalbench/main/tasks/$TASK
curl -sL $BASE/base_prompt.txt -o data/legalbench/$TASK/base_prompt.txt
curl -sL $BASE/train.tsv       -o data/legalbench/$TASK/train.tsv
curl -sL $BASE/test.tsv        -o data/legalbench/$TASK/test.tsv
```

## Licensing

LegalBench is released under the **Creative Commons Attribution 4.0
International (CC BY 4.0)** license.  Individual tasks may carry additional
licensing restrictions from their upstream sources; check the task-level
`README.md` in the HazyResearch/legalbench repository before use.

## What is NOT committed

Per-task data directories (`data/legalbench/*/`) are excluded by `.gitignore`.
Only `manifest.v1.json` and this `README.md` are tracked.  This keeps the
repository lightweight and avoids inadvertently redistributing datasets with
more restrictive upstream licenses.

To populate the data locally, download the tasks listed in `manifest.v1.json`
from the HuggingFace dataset or the GitHub repository using the pattern above.
