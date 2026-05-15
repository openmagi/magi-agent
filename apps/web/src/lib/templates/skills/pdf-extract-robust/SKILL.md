---
name: pdf-extract-robust
description: Use when accurate PDF extraction matters and you CANNOT tolerate vision OCR errors on critical values (years, page numbers, IDs, dates, amounts, exam numbers). Runs vision extraction (Firecrawl) AND text-layer extraction (pdf-parse) in parallel and cross-verifies. Surfaces any disagreement. MUST use this instead of plain document-reader when verification_mode=full or when the task involves audit/legal/financial/exam material.
user_invocable: false
metadata:
  author: openmagi
  version: "1.0"
---

# pdf-extract-robust — Cross-Signal PDF Extraction

## Why

단일 vision OCR은 모델이 다르더라도 **같은 방식으로 틀릴 수 있음** (공통 시각 편향). 특히 숫자 `2·3·5`, `6·8·0`, `1·I·l` 같은 시각적 혼동. 벡터 PDF의 **텍스트 레이어는 독립 신호** — 실제 인코딩된 문자를 읽으므로 시각 모델이 틀려도 텍스트 레이어는 맞음.

본 스킬: Firecrawl (vision + OCR) + pdfjs-dist 텍스트 레이어 병렬 실행 → critical value 교차 검증.

## When to Use

**MUST use when:**
- `<task_contract>`에 `verification_mode: full`
- 법률/재무/감사/시험/의료 자료 — 오답 시 실제 피해
- 숫자·페이지·연도·ID·인용 정확성이 결과 품질을 좌우

**DO NOT use when:**
- 단순 요약/개요만 필요한 경우 (plain document-reader 사용)
- 스캔 PDF (텍스트 레이어 없음) — 이 스킬의 교차 신호가 원리적으로 없음. 결과에 `has_text_layer: false` 오면 "vision only" 명시적 고지.
- verification_mode가 `sample`

## Usage

```bash
# 1. Vision extraction (semantic, layout-aware)
VISION_OUT=$(curl -s -X POST http://document-worker.clawy-system.svc:3009/convert \
  -H "X-Mimetype: application/pdf" \
  -H "X-Filename: $FILENAME" \
  --data-binary @"$PDF_PATH" \
  --max-time 180)

# 2. Text-layer extraction (factual, page-structured)
TEXT_OUT=$(curl -s -X POST http://document-worker.clawy-system.svc:3009/extract/text-only \
  --data-binary @"$PDF_PATH" \
  --max-time 60)

# 3. Parse text-layer JSON response
# {
#   "total_pages": 856,
#   "has_text_layer": true,
#   "avg_chars_per_page": 1840,
#   "pages": [{"page_number": 1, "text": "...", "char_count": 1840}, ...]
# }
```

## Cross-Verification Pattern

텍스트 레이어가 존재할 때 (`has_text_layer: true`):

1. **Critical value 추출**: vision 결과에서 숫자/날짜/ID 목록 뽑기
2. **Ground truth 확인**: 같은 페이지 텍스트 레이어에서 해당 값이 문자열로 존재하는지 `grep`
3. **Mismatch flag**: vision에 `"25년 6월 모의시험"`, 텍스트 레이어에 `"23년"`만 있으면 → vision 오독 의심

```bash
# 예: 페이지 494의 연도 확인
PAGE_TEXT=$(echo "$TEXT_OUT" | jq -r '.pages[493].text')  # 0-indexed array

# vision이 주장한 연도
VISION_YEAR="25"

# 텍스트 레이어에 해당 연도가 실제로 있는가
if echo "$PAGE_TEXT" | grep -qE "${VISION_YEAR}년"; then
  echo "CONFIRMED"
else
  # 텍스트 레이어에 존재하는 다른 연도를 찾아 제시
  ACTUAL=$(echo "$PAGE_TEXT" | grep -oE '[0-9]{2}년' | head -1)
  echo "MISMATCH: vision=${VISION_YEAR}년, text-layer=${ACTUAL}"
fi
```

## Fail-Open Behavior

- 텍스트 레이어 없음 (`has_text_layer: false`) → vision 결과만 사용. 최종 응답에 "텍스트 레이어 부재로 교차검증 불가 — vision OCR only, 숫자/연도/페이지 오독 가능성 존재" 명시.
- text-only endpoint 장애 → vision 결과만 사용. 마찬가지로 고지.
- 두 extraction 모두 실패 → 에러 반환, 재시도.

## Output 형식 권장

응답에 extraction 신뢰도 메타 포함:

```
[extraction_meta]
vision_source: Firecrawl Fire-PDF
text_layer: available (avg 1840 chars/page, 856 pages)
cross_verification: 67/67 critical values confirmed
  OR
cross_verification: 65/67 confirmed, 2 flagged (page 494 year mismatch, page 302 exam number)
[/extraction_meta]
```

## Relationship to Other Skills

- **document-reader**: one-shot extraction. Use when accuracy isn't mission-critical.
- **pdf-extract-robust** (this): cross-signal extraction. Use when you cannot afford silent OCR errors.
- Both call the same `document-worker` service; this one additionally hits `/extract/text-only`.

## Example End-to-End (Dongwon 물권법 Case)

```bash
# Task: 제1·2·4·5편, 23-25년, 물권법 관련 선지 문제 추출
PDF="/workspace/uploads/민법_기출.pdf"

# Vision + text-layer 병렬
V=$(curl -s -X POST http://document-worker.clawy-system.svc:3009/convert \
    -H "X-Mimetype: application/pdf" -H "X-Filename: pdf" \
    --data-binary @"$PDF" --max-time 180)
T=$(curl -s -X POST http://document-worker.clawy-system.svc:3009/extract/text-only \
    --data-binary @"$PDF" --max-time 60)

# vision에서 뽑은 각 문제의 (page, year) → text-layer에서 해당 페이지의 "YY년" 패턴 확인
# 불일치 건만 사용자에게 flag
# ...
```

## Important Rules

- **Never trust vision alone** for critical values when text layer exists
- **Always report cross-verification stats** (`M/N confirmed`) in final output when verification_mode=full
- **Surface all mismatches** — do not silently pick one. User decides.
- **Do not use this for scanned PDFs** — no text layer = no cross-signal. Fall back to plain vision + explicit disclaimer.
