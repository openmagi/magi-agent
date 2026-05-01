---
id: user-harness:final-answer-verifier
trigger: beforeCommit
action:
  type: llm_verifier
enforcement: block_on_fail
timeoutMs: 8000
---

Check whether the assistant's final answer satisfies the user's request and does not skip requested deliverables. Reply with exactly `PASS` or `FAIL: <short reason>`.
