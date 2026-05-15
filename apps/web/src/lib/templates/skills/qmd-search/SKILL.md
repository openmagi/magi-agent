---
name: qmd-search
description: Search workspace knowledge using qmd (BM25 keyword search). Use before external lookups for credentials, configs, past decisions, patterns, or resuming context.
---

# qmd — Workspace Knowledge Search

Your index: `{{BOT_NAME}}`

## Quick Reference

| Action | Command |
|--------|---------|
| Search | `qmd --index {{BOT_NAME}} search "keyword1 keyword2"` |
| Search (files only) | `qmd --index {{BOT_NAME}} search "query" --files` |
| Get file | `qmd --index {{BOT_NAME}} get "path/to/file.md"` |
| Multi-get | `qmd --index {{BOT_NAME}} multi-get "knowledge/*.md"` |
| Re-index | `qmd --index {{BOT_NAME}} update` |
| Status | `qmd --index {{BOT_NAME}} status` |

## BM25 Query Construction

qmd uses **BM25 keyword matching** — not semantic/AI search. Queries must be **keywords**, not natural language.

### Rules
1. Use **2-4 specific keywords** — more precise = better results
2. **No natural language** — strip filler words (how, what, the, is, to, a)
3. **Try variations** — if first query misses, use synonyms or related terms
4. Use `--files` first to find relevant files, then `get` to read them

### Examples

| Bad (natural language) | Good (keywords) |
|------------------------|-----------------|
| "How do I configure the database?" | "database config" |
| "What is the API key for stripe?" | "stripe key" or "stripe API" |
| "Show me the deployment process" | "deploy process" or "deployment steps" |
| "What did we decide about caching?" | "caching decision" or "cache strategy" |

### Multi-step Search Pattern

```bash
# 1. Find relevant files
qmd --index {{BOT_NAME}} search "topic" --files

# 2. Read the most relevant file
qmd --index {{BOT_NAME}} get "knowledge/relevant-file.md"

# 3. If not found, try synonyms
qmd --index {{BOT_NAME}} search "alternative keyword"
```

## When to Search

- **Before any external lookup** — check local knowledge first
- **Resuming work** — search for task context, past progress
- **Credentials/configs** — search before asking user
- **Past decisions** — search daily logs and knowledge files
- **Domain patterns** — search LESSONS.md content and knowledge/

## After Modifying Knowledge Files

Always re-index after changing files in your workspace:

```bash
qmd --index {{BOT_NAME}} update
```

## Vector Search (Pro+ Only)

If vector search is available (`QMD_VECTOR_ENABLED=true`), you can use **semantic search** for meaning-based lookups:

| Action | Command |
|--------|---------|
| Vector search | `qmd --index {{BOT_NAME}} vsearch "natural language query"` |
| Rebuild vectors | `qmd --index {{BOT_NAME}} embed` |

### When to Use Vector vs BM25

| Scenario | Use |
|----------|-----|
| Exact keyword lookup (API key, config name) | BM25: `search` |
| Conceptual query ("how does billing work") | Vector: `vsearch` |
| File/path search | BM25: `search --files` |

### Vector Query Tips
- Unlike BM25, vector search understands **natural language** — full sentences work
- Try BM25 first (faster), fall back to vsearch if no good results
- After modifying files, run both `update` and `embed` to rebuild indexes
