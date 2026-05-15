---
name: fetch-full
description: Retrieve the original full content of a previously compacted tool result. Use only when the compacted summary is insufficient — e.g., you need exact quotes, specific page content, precise numeric values, or table rows that were omitted. Do NOT call speculatively; the summary is usually enough.
---

# fetch-full — Retrieve compacted tool result

이전 턴에서 큰 도구 결과(OCR, 대용량 파일 추출, 긴 웹 크롤)가 자동 요약되고 `[FULL_RESULT_REF: ...]` 마커가 남았을 때, 원본 전체 내용을 조회합니다.

## When to Use

- 요약 하단에 `[LOSSY: N exact quotes omitted]` 마커 있음 + 정확한 인용이 필요함
- 요약에 언급된 페이지/테이블 구체 내용이 빠졌고, 그 내용이 **답변에 실제 필요함**
- 원본 수치·식별자가 정확해야 하는 재무/법률 작업

## When NOT to Use

- 요약만으로 답할 수 있을 때 (대부분의 경우)
- "혹시 모르니까" 확인용 — **비용 예산이 세션당 500KB로 제한됨**
- `[FULL_RESULT_REF: ...]` 마커가 없을 때 (호출해도 404)

## Usage

```bash
# Basic: entire content (up to 20KB)
fetch-full.sh "<signed_ref_id>"

# Paginated: retrieve slice
fetch-full.sh "<signed_ref_id>" --offset=20000 --length=20000
```

**signed_ref_id**는 compacted 결과 블록의 `[FULL_RESULT_REF: ...]`에 그대로 적혀있는 문자열. 절대 수정·재조립하지 말 것.

## Output format

JSON:
```json
{
  "content": "...",
  "total_size": 120000,
  "has_more": true,
  "offset": 0,
  "length": 20000,
  "tool_name": "document_extract"
}
```

## Limits

- **Rate**: 10 calls/minute per session
- **Byte budget**: 500KB total retrieval per session
- **TTL**: ref_id 유효기간 24시간

Budget/rate 초과 시 429 에러. 그러면 요약으로 돌아가거나 유저에게 "원본 더 필요한 경우 재업로드 요청" 메시지 출력.

## Errors

| Status | Meaning | Action |
|--------|---------|--------|
| 400 | malformed ref_id | ref_id를 정확히 복사했는지 확인 |
| 403 | session mismatch / HMAC invalid | 다른 세션의 ref_id는 조회 불가 |
| 404 | expired or missing | 요약만으로 답변 시도 |
| 429 | rate/budget exceeded | 요약 기반 답변 + 유저 고지 |
| 503 | storage unavailable | 일시 장애, 재시도 1회 후 요약 기반 답변 |

## Example Flow

```bash
# 이전 턴 도구 결과에 이런 블록이 있었음:
# --- tool_result ---
# [summary: "제4편 채권총론, p494 문74..."]
# [FULL_RESULT_REF: 4f8a...abcd.a1b2c3d4]
# [LOSSY: 47 exact quotes omitted]
# --- end ---

# 유저가 "그 문74 원문 보여줘"라고 요청
SIGNED_REF="4f8a1234-5678-90ab-cdef-000000000001.a1b2c3d4"
fetch-full.sh "$SIGNED_REF" --offset=0 --length=20000
```
