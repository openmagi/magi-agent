---
name: email-channel
description: Use when the user asks about your email address, wants to check the email inbox, send an email, or when responding to messages from the email channel. This is the platform email (default). For creating NEW x402 private email inboxes, use the agentmail-x402 skill instead.
metadata:
  author: openmagi
  version: "2.1"
---

# Email Channel (플랫폼 이메일)

너는 이메일 인박스가 있는 AI 에이전트다. 이메일을 받고, 읽고, 보낼 수 있다.

> **참고:** 이것은 **플랫폼 이메일**이다 — clawy 플랫폼이 관리하는 기본 이메일이다.
> 유저가 "새 이메일 만들어줘", "x402 이메일", "AgentMail", "프라이빗 이메일"을 요청하면
> `agentmail-x402` 스킬을 사용하라 (별개의 시스템이다).

## Your Email Address

너의 이메일 주소는 환경변수에 저장되어 있다:

```bash
echo $BOT_EMAIL_ADDRESS
```

누군가 "네 이메일 주소가 뭐야?" 같은 질문을 하면 위 명령어를 실행해서 확인하고 알려줘라.
`$BOT_EMAIL_ADDRESS`가 비어있으면 이메일이 아직 활성화되지 않은 것이다. 유저에게 Settings에서 이메일을 활성화하라고 안내하라.

## Email API

모든 이메일 작업은 내부 API를 통해 수행한다. 인증은 `$GATEWAY_TOKEN` 환경변수를 사용한다.

API Base URL: `http://chat-proxy.clawy-system.svc.cluster.local:3002`

### 인박스 확인 (메시지 목록)

```bash
curl -s -H "Authorization: Bearer $GATEWAY_TOKEN" \
  "http://chat-proxy.clawy-system.svc.cluster.local:3002/v1/bot-email/inbox?limit=20&offset=0"
```

응답은 JSON 배열이다. 각 메시지에는 `id`, `from`, `to`, `subject`, `text`, `created_at` 등이 포함된다.

### 특정 메시지 읽기

```bash
curl -s -H "Authorization: Bearer $GATEWAY_TOKEN" \
  "http://chat-proxy.clawy-system.svc.cluster.local:3002/v1/bot-email/message/{message_id}"
```

### 이메일 보내기

```bash
curl -s -X POST \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"to":["recipient@example.com"],"subject":"제목","text":"본문 내용"}' \
  "http://chat-proxy.clawy-system.svc.cluster.local:3002/v1/bot-email/send"
```

**필수 필드:**
- `to`: 수신자 이메일 주소 배열 (예: `["user@example.com"]`)
- `subject`: 이메일 제목
- `text`: 이메일 본문 (plain text)

## When This Applies

- 누군가 너의 이메일 주소를 물어볼 때
- "인박스 확인해줘", "메일 왔어?", "새 메일 있어?" 같은 요청
- "이메일 보내줘", "메일 써줘" 같은 요청
- 시스템 메시지에 `[Channel: email]`이 포함된 경우 (이메일 답장 모드)

## Responding to Emails (Channel Mode)

시스템 메시지에 `[Channel: email]`이 있으면 너는 이메일에 답장하는 중이다.

### Email Context

시스템 메시지에서 다음 정보를 확인:
- `[Email From: ...]` — 발신자 이메일 주소
- `[Email Subject: ...]` — 이메일 제목

user 메시지가 이메일 본문이다.

### Response Format

이메일 답장은 **plain text**로 작성한다. HTML, Markdown 사용 금지.

```
[인사]

[본문 — 질문/요청에 대한 답변]

[마무리]
```

### Rules

1. **인사**: 발신자에게 적절한 인사 (예: "안녕하세요," / "Hi,")
2. **본문**: 질문이나 요청에 직접적으로 답변. 간결하고 명확하게.
3. **마무리**: 정중한 마무리 (예: "감사합니다." / "Best regards,")
4. **서명**: 없음 — 시스템이 자동으로 처리
5. **언어**: 발신자가 사용한 언어로 답변. 한국어 메일이면 한국어, 영어면 영어.
6. **톤**: 전문적이되 친근하게. 과도한 형식은 불필요.
7. **길이**: 필요한 만큼만. 짧은 질문에 장문 답변 금지.
8. **Markdown/HTML 금지**: `**bold**`, `# heading`, `<br>` 등 사용하지 않는다. 순수 텍스트만.
9. **코드**: 코드를 포함해야 하면 들여쓰기로 구분 (``` 사용 금지)
10. **링크**: URL은 그대로 텍스트로 포함

## Capabilities

- 너의 이메일 주소를 알려줌 (`$BOT_EMAIL_ADDRESS`)
- 인박스 확인 (메시지 목록 조회)
- 특정 메시지 상세 읽기
- 새 이메일 보내기 (누구에게든)
- 수신 이메일에 자동 답장 (시스템이 채널 모드로 전달)

## Limitations

- 첨부파일은 지원하지 않는다. 파일이 필요하면 다른 방법을 안내하라.
- 이메일은 plain text만 지원한다 (HTML 포맷팅 불가).
- 이메일 쿼타가 있다. 초과 시 크레딧에서 차감된다.

## Important

- 이메일을 보낼 때는 반드시 유저의 명시적 요청이 있어야 한다.
- 민감한 정보(비밀번호, API 키 등)를 이메일로 보내지 마라.
- 스팸이나 대량 발송을 하지 마라.
- Channel 모드(`[Channel: email]`)에서는 직접 보내지 않는다 — 너의 응답 텍스트가 자동으로 회신된다.
