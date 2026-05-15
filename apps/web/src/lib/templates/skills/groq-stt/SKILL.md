---
name: groq-stt
description: Use when transcribing voice messages or audio files to text, converting speech to text, or when a user sends a voice/audio message that needs to be understood.
---

# Groq Whisper Speech-to-Text

Groq API로 초고속 음성 인식(STT)을 수행한다. Whisper large-v3 모델, 100+ 언어 지원.

## When to Use

- 사용자가 음성 메시지를 보낸 경우 (자동 트랜스크립션)
- 오디오 파일(.mp3, .wav, .ogg, .m4a 등)을 텍스트로 변환
- 음성에서 언어를 감지해야 할 때
- 오디오를 영어로 번역해야 할 때

## API Endpoint

**Base URL**: `https://api.groq.com/openai/v1`

**인증**: `Authorization: Bearer $GROQ_API_KEY` 헤더

### 1. 음성 → 텍스트 변환 (Transcription)

```
system.run ["curl", "-s", "-X", "POST", "https://api.groq.com/openai/v1/audio/transcriptions",
  "-H", "Authorization: Bearer $GROQ_API_KEY",
  "-F", "file=@/path/to/audio.ogg",
  "-F", "model=whisper-large-v3",
  "-F", "response_format=verbose_json"]
```

Parameters:
- `file` (필수): 오디오 파일 (multipart upload)
- `model` (필수): `whisper-large-v3` (다국어), `whisper-large-v3-turbo` (빠름), `distil-whisper-large-v3-en` (영어 전용)
- `language`: ISO 639-1 코드 (`ko`, `en`, `ja` 등) — 생략 시 자동 감지
- `response_format`: `json` (기본), `verbose_json` (타임스탬프 포함), `text` (텍스트만)
- `temperature`: 0-1 (기본 0) — 낮을수록 정확

Response (`verbose_json`):
```json
{
  "text": "안녕하세요, 오늘 날씨가 좋네요.",
  "language": "ko",
  "duration": 3.42,
  "segments": [...]
}
```

### 2. 음성 → 영어 번역 (Translation)

```
system.run ["curl", "-s", "-X", "POST", "https://api.groq.com/openai/v1/audio/translations",
  "-H", "Authorization: Bearer $GROQ_API_KEY",
  "-F", "file=@/path/to/audio.ogg",
  "-F", "model=whisper-large-v3"]
```

영어가 아닌 음성을 영어 텍스트로 번역한다.

## 모델 선택

| Model ID | 언어 | 속도 | 용도 |
|----------|------|------|------|
| `whisper-large-v3` | 100+ | 빠름 | 다국어, 기본 추천 |
| `whisper-large-v3-turbo` | 100+ | 매우 빠름 | 저지연 필요 시 |
| `distil-whisper-large-v3-en` | 영어만 | 가장 빠름 | 영어 전용 |

## Workflow

1. **음성/오디오 파일 수신 감지**: 사용자가 voice message 또는 audio file을 보내면 자동 실행
2. **파일 경로 확인**: 수신된 파일의 로컬 경로 확인
3. **트랜스크립션 실행**: `system.run curl`로 Groq Whisper API 호출
4. **결과 전달**: 트랜스크립션 텍스트를 사용자에게 전달
5. **내용 응답**: 음성 메시지의 내용을 이해하고 적절히 응답

## 음성 메시지 자동 처리 규칙

음성 메시지나 오디오 파일을 수신하면:
1. 즉시 트랜스크립션을 수행한다
2. 트랜스크립션 결과를 인용하여 보여준다: `> 🎤 "트랜스크립션 텍스트"`
3. 내용에 대해 자연스럽게 응답한다
4. 트랜스크립션 실패 시 오류를 안내한다

## API 키가 없는 경우

`$GROQ_API_KEY` 환경변수가 설정되어 있지 않으면:
1. 사용자에게 Groq API 키가 필요하다고 안내한다
2. 무료로 발급 가능: https://console.groq.com → API Keys
3. 사용자가 키를 제공하면 해당 키로 API를 호출한다

## Red Flags

- 파일 크기 제한: 최대 25 MB
- 지원 포맷: mp3, mp4, mpeg, mpga, m4a, wav, webm, ogg
- 텔레그램 음성 메시지는 `.ogg` (Opus codec) — 지원됨
- 응답은 JSON — 바이너리가 아님
- `translation` 엔드포인트는 영어로만 번역 (다른 언어 쌍 불가)
- `$GROQ_API_KEY` 환경변수 없으면 위 "API 키가 없는 경우" 절차를 따른다
- 빈 오디오나 무음 파일은 빈 텍스트 반환 가능
