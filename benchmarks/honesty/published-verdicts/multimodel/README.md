# Multi-model audit trail

Per agent model (`sonnet` = claude-sonnet-4-6, `gemini` = gemini-3.1-pro):
- `ground_truth.json` — per-turn population + evidence-bound verdict + claim type.
- `prompts_answer.json` / `prompts_transcript.json` — exact judge prompts
  (`llm_judge.build_judge_prompt`), keyed `taskid|tone`.
- `verdicts_answer.json` / `verdicts_transcript.json` — Claude Opus 4.8 SHIP/FLAG
  per `taskid|tone`.

gpt-5.5's trail is the sibling `../` directory (keyed per-tone-file, not `|`).
`python -m benchmarks.honesty.multimodel` reads all three and prints the pooled
table in `../../RESULT-v3-multimodel.md`.
