---
name: knowledge-search
description: Search and inspect Personal KB and Org KB content. Use when the user asks about uploaded documents, organization knowledge, source files, or document-backed answers.
user_invocable: true
metadata:
  author: openmagi
  version: "3.2"
---

# Knowledge Base Search

Use `kb-search.sh` for KB work. It safely handles Korean text, quotes, and
newlines, and it searches both Personal KB and accessible Org KB through the
same scoped runtime.

## Commands

List accessible collections:

```bash
system.run ["sh", "-c", "kb-search.sh --collections"]
```

Inspect the KB manifest across Personal KB and Org KB:

```bash
system.run ["sh", "-c", "kb-search.sh --manifest"]
```

Inspect one collection as a Markdown guide:

```bash
system.run ["sh", "-c", "kb-search.sh --guide 'COLLECTION_NAME'"]
```

List documents in one collection:

```bash
system.run ["sh", "-c", "kb-search.sh --documents 'COLLECTION_NAME'"]
```

Search all accessible collections:

```bash
system.run ["sh", "-c", "kb-search.sh 'KEYWORDS'"]
```

Search one collection:

```bash
system.run ["sh", "-c", "kb-search.sh 'COLLECTION_NAME' 'KEYWORDS' 10"]
```

Fetch exact converted Markdown when a result or manifest gives an object key:

```bash
system.run ["sh", "-c", "kb-search.sh --get 'OBJECT_KEY_CONVERTED'"]
```

## Auto-Awareness Protocol

On session start, check whether KB collections exist:

```bash
system.run ["sh", "-c", "kb-search.sh --collections"]
```

If collections exist, write a short summary to `SCRATCHPAD.md` under
`## Knowledge Base`.

On every user message:

1. Decide whether the question could relate to Personal KB or Org KB.
2. If yes, search KB before answering.
3. If the question is clearly unrelated, answer normally.

## Search Strategy

- Use 2-4 specific keywords. The current search is keyword-oriented.
- Search multiple times with different terms for broad questions.
- Omit the collection argument to search across both Personal KB and Org KB.
- Use collection-specific search when the user names a known collection.
- Increase `top_k` up to 20 for broader sweeps.

Good:

```bash
system.run ["sh", "-c", "kb-search.sh '연대채무 변제충당'"]
```

Good with a collection:

```bash
system.run ["sh", "-c", "kb-search.sh '한빛전자 2026 감사' '시산표 매출채권' 20"]
```

## Manifest Fallback Protocol

Do not conclude that KB material is absent after one empty search.

When a search returns few or no results:

1. Run `kb-search.sh --manifest` or `kb-search.sh --manifest 'COLLECTION_NAME'`.
2. Inspect `canonical_title`, `aliases`, `path`, `source_provider`, and
   `source_external_id`.
3. Retry search with the best alias, extensionless title, path segment, or source
   ID.
4. If the manifest exposes `object_key_converted` for the likely document, use
   `kb-search.sh --get 'OBJECT_KEY_CONVERTED'` to inspect the exact Markdown.
5. Only then say the KB does not contain the requested material.

This matters for Korean filenames, Notion titles, folder paths, and Unicode
normalization differences.

## Cross-Document Analysis Pattern

For requests like "전부 모아줘", "전체 내용 요약", or "빈출 논점 정리":

1. Run `kb-search.sh --manifest` to understand collections and documents.
2. Use `kb-search.sh --guide 'COLLECTION_NAME'` for likely collections.
3. Search with multiple keyword angles.
4. Deduplicate by document/source.
5. Present an organized answer with citations.

## Citation Rules

1. Never fabricate content.
2. Cite sources from search results or fetched Markdown.
3. If material is not found after the fallback protocol, say so clearly.
4. Prefer direct excerpts when the source text is concise and relevant.

Supported upload formats: PDF, DOCX, XLSX, HWPX, CSV, TXT, MD, JSON.
