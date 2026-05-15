---
name: evidence-router
description: Use when a request needs accurate, current, cited, or source-grounded facts; routes claims through web search, KB search, file reads, document extraction, or direct tool checks before answering
---

# Evidence Router

Use this skill before making factual claims that depend on current state, private knowledge, uploaded files, workspace files, legal/financial/medical facts, prices, schedules, policies, or exact numbers.

## Route

1. Identify the claim type:
   - Current/public facts: use web or official source search.
   - User/private knowledge: use KB search.
   - Workspace/project facts: use file search and file read.
   - Uploaded documents or PDFs: use document-reader or pdf-extract-robust.
   - Runtime/platform state: use direct tools or service health checks.
2. Prefer primary sources over summaries.
3. If evidence is unavailable, say what was checked and what remains unknown.
4. Cite, name, or summarize the evidence source when the user needs trust.

## Stop Conditions

- Do not answer from memory when the requested fact could be stale.
- Do not cite a source you did not inspect.
- Do not turn a sample into an exhaustive claim.
