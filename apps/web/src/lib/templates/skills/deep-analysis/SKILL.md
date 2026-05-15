---
name: deep-analysis
description: Deep document analysis with citation — multi-doc cross-reference, structured summary, key data extraction. Use when user asks to analyze, summarize, compare, or review documents/files. Triggers on keywords like analyze, summary, compare, review, cross-reference, insights, key points, 분석, 요약, 비교, 검토, 핵심.
user_invocable: true
metadata:
  author: openmagi
  version: "1.0"
---

# Deep Analysis

NotebookLM-level document analysis with citation-backed answers.

## When to Use
- User asks to analyze, summarize, or compare documents
- User asks for key insights, data points, or patterns from their files
- User uploads files and asks "이거 분석해줘", "요약해줘", "비교해줘"

## Procedure

### Step 1: Collect Documents

**Priority: attachments first, then KB**

If the user attached files in this message:
```bash
# Files are already converted to text by document-worker
# Access via the attachment content in the conversation
```

If no attachments, search knowledge base:
```bash
system.run ["sh", "-c", "kb-search.sh '검색 키워드'"]
```

If specific documents are referenced by name:
```bash
system.run ["sh", "-c", "kb-search.sh --doc '문서명'"]
```

Combine all sources. If total text exceeds 50K tokens, prioritize the most relevant documents.

### Step 2: Dispatch Analysis Subagent

**MANDATORY: Use subagent for analysis. Do NOT analyze directly.**

```bash
agent-run.sh --model gemini/gemini-3.1-pro-preview --max-output 15000 \
  "You are a document analyst. Analyze the following documents with STRICT citation requirements.

DOCUMENTS:
[paste collected document text here]

USER REQUEST:
[paste user's original request]

ANALYSIS INSTRUCTIONS:
1. For EACH document, provide a concise summary (200 chars max).
2. Extract key data points (numbers, dates, names, amounts).
3. If multiple documents: identify commonalities, differences, and contradictions.
4. Answer the user's specific question using ONLY information from the documents.
5. EVERY claim MUST include a citation: [Source: document_name, section/page].
6. If information is NOT in the documents, say so explicitly. Do NOT hallucinate.

OUTPUT FORMAT:
## Document Overview
| Document | Type | Key Topic | Size |
|----------|------|-----------|------|
| ... | ... | ... | ... |

## Key Findings
(citation-backed bullet points)

## Cross-Reference Analysis (if multi-doc)
(commonalities, differences, contradictions)

## Detailed Answer
(response to user's question with citations)

## Data Points Extracted
(structured table of numbers, dates, amounts with sources)"
```

### Step 3: Verify & Deliver

1. Check subagent output has actual citations (not fabricated)
2. If citations are missing, re-run with stronger instruction
3. Save analysis to knowledge for future reference:
```bash
system.run ["sh", "-c", "cat > /workspace/knowledge/analysis-$(date +%Y%m%d-%H%M).md << 'ANALYSIS_EOF'\n[analysis content]\nANALYSIS_EOF"]
```
4. Deliver result to user

### Step 4: Follow-up Readiness

After delivering analysis, be ready for:
- "이 부분 더 자세히" → drill into specific section
- "다른 관점에서 분석해봐" → re-analyze with different frame
- "이걸 보고서로 만들어줘" → format as formal report
- "엑셀로 정리해줘" → extract to spreadsheet

## Rules

- **Citation is NON-NEGOTIABLE.** Every factual claim must have [Source: ...].
- **Never fabricate information.** If it's not in the documents, say "문서에 해당 정보 없음".
- **Use Gemini for analysis.** 1M context window handles large documents better.
- **Cache results.** Save to knowledge/ so repeated questions get instant answers.
- **Respect the user's language.** If they ask in Korean, respond in Korean.
