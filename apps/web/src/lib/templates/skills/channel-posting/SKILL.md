---
name: channel-posting
description: Use when the user asks to post content to a mobile app channel, schedule regular channel posts, or when a TASK-QUEUE item involves channel posting.
metadata:
  author: openmagi
  version: "1.0"
---

# Channel Posting

너는 모바일 앱의 채널에 메시지를 능동적으로 보낼 수 있는 AI 에이전트다.

## Channel API

모든 채널 작업은 내부 API를 통해 수행한다. 인증은 `$GATEWAY_TOKEN` 환경변수를 사용한다.

API Base URL: `http://chat-proxy.clawy-system.svc.cluster.local:3002`

### 채널 목록 조회

```bash
curl -s -H "Authorization: Bearer $GATEWAY_TOKEN" \
  "http://chat-proxy.clawy-system.svc.cluster.local:3002/v1/bot-channels/list"
```

응답: `{ "channels": [{ "name": "news", "display_name": "News", "category": "Info" }, ...] }`

### 채널에 메시지 포스팅

```bash
curl -s -X POST \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"channel":"news","content":"오늘의 뉴스 요약입니다.\n\n1. ..."}' \
  "http://chat-proxy.clawy-system.svc.cluster.local:3002/v1/bot-channels/post"
```

**필수 필드:**
- `channel`: 채널 이름 (예: `"news"`, `"daily-update"`, `"reminder"`)
- `content`: 메시지 내용 (최대 8000자)

## TASK-QUEUE Pattern

유저가 정기적 채널 포스팅을 요청하면 TASK-QUEUE.md에 등록한다:

```markdown
## Scheduled Channel Posts
- [ ] 매일 09:00 — news 채널에 뉴스 요약 포스팅
- [ ] 매일 22:00 — daily-update 채널에 하루 정리
- [ ] 매주 월요일 08:00 — finance 채널에 주간 시장 리뷰
```

Heartbeat에서 TASK-QUEUE를 확인하고, 시간이 되면 해당 작업을 실행한다.

### 작업 실행 흐름

1. TASK-QUEUE.md에서 현재 시간에 해당하는 채널 포스트 작업 확인
2. 채널 목록 조회로 채널 존재 확인
3. 콘텐츠 생성 (뉴스 검색, 요약, 분석 등)
4. 채널에 포스팅
5. SCRATCHPAD.md에 실행 기록 남기기

## When This Applies

- "news 채널에 뉴스 보내줘"
- "매일 아침 daily-update에 정리해줘"
- "reminder 채널에 알림 보내줘"
- Heartbeat에서 TASK-QUEUE 확인 시 채널 포스트 작업이 있을 때

## Rules

1. **채널 확인**: 포스팅 전에 반드시 채널 목록을 조회하여 채널이 존재하는지 확인하라.
2. **스팸 금지**: 같은 채널에 짧은 시간 내 반복 포스팅하지 마라. 최소 1시간 간격을 유지.
3. **콘텐츠 품질**: 유용하고 정리된 콘텐츠만 포스팅하라. 테스트 메시지나 무의미한 내용 금지.
4. **길이 제한**: 콘텐츠는 8000자를 넘기지 마라. 긴 내용은 요약하라.
5. **유저 동의**: 정기 포스팅은 유저의 명시적 요청이 있어야 한다.
6. **기록**: 포스팅 후 SCRATCHPAD.md에 간단히 기록하라 (채널, 시간, 요약).

## Limitations

- 이미지나 파일은 포스팅할 수 없다. 텍스트만 지원.
- 채널당 최대 500개 메시지가 보관된다. 오래된 메시지는 자동 삭제.
- 존재하지 않는 채널에는 포스팅할 수 없다.
