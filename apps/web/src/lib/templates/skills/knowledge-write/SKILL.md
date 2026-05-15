---
name: knowledge-write
description: Write to your knowledge base — create collections, add/update/delete documents. Use when you need to save research results, learned information, or any structured data for future reference.
user_invocable: true
metadata:
  author: openmagi
  version: "1.0"
---

# Knowledge Base Write

Use `kb-write.sh` (always in PATH) to write to your knowledge base. All content is stored as markdown.

## Commands

```bash
# Create a new collection
system.run ["sh", "-c", "kb-write.sh --create-collection '리서치 노트'"]

# Add a document to a collection
system.run ["sh", "-c", "kb-write.sh --add '리서치 노트' 'meeting-2026-04-12.md' '# 회의 요약\n\n## 주요 결정사항\n- 항목 1\n- 항목 2'"]

# Add from a file (stdin mode)
system.run ["sh", "-c", "cat /workspace/report.md | kb-write.sh --add '리서치 노트' 'report.md' --stdin"]

# Update an existing document (overwrites)
system.run ["sh", "-c", "kb-write.sh --update '리서치 노트' 'meeting-2026-04-12.md' '# 수정된 회의 요약\n\n내용...'"]

# Delete a document
system.run ["sh", "-c", "kb-write.sh --delete '리서치 노트' 'old-notes.md'"]

# Delete an entire collection (WARNING: irreversible)
system.run ["sh", "-c", "kb-write.sh --delete-collection '임시 데이터'"]
```

## When to Use

- **Research results**: Save web search summaries, analysis findings
- **Meeting notes**: Structure and store conversation summaries
- **Learned preferences**: User preferences, recurring patterns
- **Reference data**: Curated facts, contact info, project context
- **Task logs**: Completed work summaries for future reference

## Response Format

```json
{"ok": true, "message": "Document added", "collection": "리서치 노트", "filename": "report.md"}
{"ok": false, "error": "Document already exists. Use --update to overwrite."}
{"ok": false, "error": "Knowledge Base storage quota exceeded"}
```

## Best Practices

1. **Use descriptive filenames** — `client-meeting-2026-04-12.md` not `notes.md`
2. **Structure content as markdown** — headings, lists, code blocks
3. **One topic per document** — easier to search and update later
4. **Include metadata in content** — date, source, context
5. **Use collections to organize** — group by project, topic, or client
6. **Clean up old data** — delete outdated documents to save quota

## Paired with knowledge-search

After writing documents, they are immediately searchable via `kb-search.sh`:
```bash
# Write
system.run ["sh", "-c", "kb-write.sh --add '프로젝트 A' 'findings.md' '# 조사 결과\n\n핵심 발견...'"]

# Search later
system.run ["sh", "-c", "kb-search.sh '프로젝트 A' '핵심 발견'"]
```

## Quota

Storage is limited by your plan. If quota is exceeded, delete unused documents or upgrade.
