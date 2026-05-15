---
name: deepl-translation
description: Use when translating text between languages, creating multilingual content, or needing high-quality machine translation. Also use for document translation or language detection.
---

# DeepL Translation API

DeepL API로 고품질 번역을 수행한다. 36개 언어 지원, 무료 50만자/월.

## When to Use

- 텍스트 번역 (단문/장문)
- 문서 번역 (PDF, DOCX, PPTX 등)
- 다국어 콘텐츠 생성
- 격식체/비격식체 제어가 필요한 번역
- 언어 감지

## API Endpoints

**Base URL:**
- Free: `https://api-free.deepl.com/v2`
- Pro: `https://api.deepl.com/v2`

**인증**: `Authorization: DeepL-Auth-Key $DEEPL_API_KEY` 헤더. `$DEEPL_API_KEY`는 사용자가 Settings > API Keys에서 DeepL 키를 등록하면 provisioning-worker가 봇 env에 주입한다 (BYO 키 — 현재 플랫폼 프록시 미지원; 키 미등록 시 이 스킬은 실패한다).

### 1. 텍스트 번역

```
system.run ["curl", "-s", "-X", "POST", "https://api-free.deepl.com/v2/translate",
  "-H", "Authorization: DeepL-Auth-Key $DEEPL_API_KEY",
  "-H", "Content-Type: application/json",
  "-d", "{\"text\":[\"Hello, how are you?\"],\"target_lang\":\"KO\"}"]
```

Parameters:
- `text`: 번역할 텍스트 배열 (필수)
- `target_lang`: 목표 언어 코드 (필수)
- `source_lang`: 원본 언어 (생략 시 자동 감지)
- `formality`: `default`, `more` (격식), `less` (비격식), `prefer_more`, `prefer_less`
- `context`: 번역 문맥 힌트 (번역 품질 개선)
- `model_type`: `quality_optimized` (기본), `latency_optimized`

Response:
```json
{
  "translations": [{
    "detected_source_language": "EN",
    "text": "안녕하세요, 어떻게 지내세요?"
  }]
}
```

### 2. 사용량 조회

```
system.run ["curl", "-s", "https://api-free.deepl.com/v2/usage",
  "-H", "Authorization: DeepL-Auth-Key $DEEPL_API_KEY"]
```

Response: `character_count` (사용량), `character_limit` (한도)

### 3. 지원 언어 조회

```
system.run ["curl", "-s", "https://api-free.deepl.com/v2/languages?type=target",
  "-H", "Authorization: DeepL-Auth-Key $DEEPL_API_KEY"]
```

## 주요 언어 코드

| Code | Language | Code | Language |
|------|----------|------|----------|
| `KO` | 한국어 | `EN-US` | 영어(미국) |
| `EN-GB` | 영어(영국) | `JA` | 일본어 |
| `ZH-HANS` | 중국어(간체) | `ZH-HANT` | 중국어(번체) |
| `DE` | 독일어 | `FR` | 프랑스어 |
| `ES` | 스페인어 | `PT-BR` | 포르투갈어(브라질) |
| `IT` | 이탈리아어 | `RU` | 러시아어 |
| `AR` | 아랍어 | `ID` | 인도네시아어 |

격식 지원 언어: DE, FR, IT, ES, NL, PL, PT-BR, PT-PT, JA, RU, KO

## Workflow

1. **언어 확인**: `/v2/languages` 로 지원 언어 조회
2. **번역 실행**: `/v2/translate` POST 요청
3. **결과 확인**: `translations[0].text` 에서 번역 결과 추출
4. **사용량 체크**: `/v2/usage` 로 남은 한도 확인

## Red Flags

- Free API key는 `api-free.deepl.com` 사용 (api.deepl.com 아님)
- 무료 한도: 월 500,000자 — `/v2/usage`로 확인
- 요청 본문 최대 128 KiB — 긴 텍스트는 분할 필요
- `target_lang`에 지역 변형 사용 가능 (`EN-US` vs `EN-GB`, `PT-BR` vs `PT-PT`)
- `source_lang`은 지역 변형 없이 사용 (`EN`, `PT`, `ZH`)
- HTTP 429: 요청 과다 → 잠시 후 재시도
- HTTP 456: 월 한도 초과
