---
name: elevenlabs-tts
description: Use when generating speech from text, creating voice narration, producing audio content, or converting text to natural-sounding speech. Also use for multilingual voice synthesis or when high-quality TTS is needed.
---

# ElevenLabs Text-to-Speech API

ElevenLabs API로 고품질 음성 합성(TTS)을 수행한다. 70+ 언어, 45+ 프리메이드 보이스, 다양한 모델 지원.

## When to Use

- 텍스트를 자연스러운 음성으로 변환
- 다국어 음성 합성 (한국어, 일본어, 영어 등)
- 뉴스/보고서/데이터 읽기 음성 생성
- 교육/학습 콘텐츠 음성 제작
- 실시간 스트리밍 TTS

## API Endpoint

**Base URL**: `https://api.elevenlabs.io/v1`

**인증**: `xi-api-key` 헤더 (elevenlabs.io 가입 후 발급)

### 1. 텍스트 → 음성 변환

```bash
curl -X POST "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM" \
  -H "xi-api-key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "안녕하세요, 오늘의 경제 브리핑입니다.",
    "model_id": "eleven_multilingual_v2",
    "voice_settings": { "stability": 0.5, "similarity_boost": 0.75 }
  }' --output speech.mp3
```

**Path**: `/v1/text-to-speech/{voice_id}`

**Query Parameters**:
- `output_format`: `mp3_44100_128` (기본), `mp3_22050_32` (경량), `pcm_16000`, `pcm_44100`

**Body (JSON)**:
- `text` (필수): 변환할 텍스트
- `model_id`: 모델 선택 (기본 `eleven_multilingual_v2`)
- `language_code`: ISO 639-1 (`ko`, `en`, `ja`, `zh`, `de`, `fr` 등)
- `voice_settings`:
  - `stability` (0-1): 낮을수록 표현적, 높을수록 일관적
  - `similarity_boost` (0-1): 원본 보이스 유사도
  - `style` (0-1): 스타일 강도
  - `speed` (double): 재생 속도 (1.0 = 보통)

**Response**: 바이너리 오디오 데이터 (`audio/mpeg`)

### 2. 스트리밍 TTS

```bash
curl -X POST "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM/stream" \
  -H "xi-api-key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "실시간 스트리밍 음성입니다.", "model_id": "eleven_flash_v2_5"}' \
  --output - | ffplay -nodisp -autoexit -
```

### 3. 보이스 목록

```
web_fetch "https://api.elevenlabs.io/v2/voices?page_size=50&voice_type=premade" (with xi-api-key header)
```

### 4. 모델 목록

```
web_fetch "https://api.elevenlabs.io/v1/models" (with xi-api-key header)
```

## 모델 선택

| Model ID | 언어 | 최대 글자 | 지연 | 용도 |
|----------|------|-----------|------|------|
| `eleven_multilingual_v2` | 29개 | 10,000 | 보통 | 다국어, 기본 추천 |
| `eleven_flash_v2_5` | 32개 | 40,000 | ~75ms | 초저지연, 실시간용 |
| `eleven_turbo_v2_5` | 32개 | 40,000 | ~250ms | 빠르고 고품질 |
| `eleven_v3` | 70+ | 5,000 | 보통 | 최신, 대화형 |

## 주요 프리메이드 보이스

| Voice | ID | 스타일 |
|-------|----|--------|
| Rachel | `21m00Tcm4TlvDq8ikWAM` | 차분, 내레이션 |
| Adam | `pNInz6obpgDQGcFmaJgB` | 깊은 목소리, 내레이션 |
| Brian | `nPczCjzI2devNBz1zQrb` | 깊은 목소리, 내레이션 |
| Daniel | `onwK4e9ZLuTAKqWW03F9` | 권위적, 뉴스 |
| Charlotte | `XB0fDUnXU5powFXDhCwa` | 매력적, 영상 |
| Lily | `pFZP5JQG7iQjIQuC4Bku` | 따뜻한, 내레이션 |
| Sarah | `EXAVITQu4vr4xnSDxMaL` | 부드러운, 뉴스 |

## Voice Settings 가이드

| 설정 | 값 | 효과 |
|------|-----|------|
| stability: 0.3 | 낮음 | 표현적, 스토리텔링 |
| stability: 0.7 | 높음 | 일관적, 뉴스/데이터 |
| similarity_boost: 0.5 | 중간 | 균형잡힌 보이스 |
| similarity_boost: 0.9 | 높음 | 원본에 가까움 |
| speed: 1.0 | 보통 | 기본 속도 |
| speed: 1.2 | 빠름 | 데이터 리딩용 |

## Workflow

1. **보이스 선택**: 위 테이블 또는 `/v2/voices` 조회
2. **모델 선택**: 다국어 → `eleven_multilingual_v2`, 실시간 → `eleven_flash_v2_5`
3. **TTS 생성**: `/v1/text-to-speech/{voice_id}`로 오디오 생성
4. **설정 조정**: `voice_settings`로 stability/similarity 튜닝

## Red Flags

- 무료 tier: 10,000 chars/month (~10분 음성) — 비상업적 용도만
- 응답은 바이너리 오디오 — JSON이 아님, 파일로 저장 필요
- `xi-api-key` 헤더로 인증 — 쿼리 파라미터 아님
- 한국어는 `eleven_multilingual_v2` 또는 `eleven_flash_v2_5` 모델 사용
- 긴 텍스트는 청크로 분할하여 순차 생성 권장
- 응답 헤더 `x-character-count`로 사용량 확인 가능
