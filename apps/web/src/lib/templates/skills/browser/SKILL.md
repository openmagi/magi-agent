---
name: browser
description: Use when you need to interact with a website beyond simple fetching — clicking buttons, filling forms, navigating multi-page flows, reading JS-rendered content, or performing any task that requires a real browser. NOT needed for simple URL fetching (use web_fetch) or content extraction (use Firecrawl).
---

# Browser Automation

원격 브라우저 세션을 통해 웹사이트를 직접 조작합니다. 버튼 클릭, 폼 입력, 페이지 이동, JS 렌더링된 콘텐츠 읽기 등.

## Primary Interface

Use the native `Browser` tool first in core-agent. It owns session creation,
CDP endpoint handling, `agent-browser` calls, and workspace-safe screenshots.

Do not say browser automation is unavailable unless the native `Browser` tool
returns a concrete runtime error. The core-agent image does not bundle Chromium;
Chromium runs in the centralized `browser-worker`.

The shell commands below are fallback diagnostics only, or for cases where the
native tool is unavailable in an older runtime.

## When to Use

- 로그인이 필요한 사이트 조회
- 버튼 클릭, 폼 제출 등 인터랙션이 필요한 작업
- JS-heavy SPA에서 콘텐츠 추출
- 다단계 웹 프로세스 (신청, 예약 등)

## When NOT to Use

- 단순 URL 내용 읽기 → `web_fetch` 사용
- API가 있는 서비스 → `integration.sh` 사용
- 정적 콘텐츠 추출 → Firecrawl 사용
- 로그인/권한/대량 수집이 필요한 플랫폼 데이터 → 먼저 CSV/XLSX/export, 공식 API, 또는 승인된 provider connector를 요청

## Cost

- 세션 생성: 1¢
- 커맨드당: 0.1¢
- 비용에 유의하여 필요한 커맨드만 실행하세요

## Session Lifecycle

1. **세션 생성** → `integration.sh browser/session-create`
2. **커맨드 실행** → `agent-browser` CLI 사용
3. **세션 종료** → `integration.sh browser/session-close?sessionId=<id>`

세션은 5분 idle 후 자동 종료, 최대 30분 유지.

---

## Step 1: Create Session

```bash
RESULT=$(integration.sh browser/session-create)
SESSION_ID=$(echo "$RESULT" | jq -r '.sessionId')
CDP_ENDPOINT=$(echo "$RESULT" | jq -r '.cdpEndpoint')
```

**중요:** `CDP_ENDPOINT`는 세션별 인증 토큰을 포함한 완전한 URL입니다. 이 값을 그대로 사용하세요. `$BROWSER_CDP_URL`을 기반으로 직접 URL을 구성하면 401 Unauthorized로 실패합니다.

## Step 2: Browse with agent-browser

### Open a page
```bash
agent-browser --cdp "$CDP_ENDPOINT" open "https://example.com"
```

### Take a snapshot (accessibility tree with refs)
```bash
agent-browser --cdp "$CDP_ENDPOINT" snapshot
```

Output example:
```
- heading "Example Domain" [ref=e1]
- paragraph "This domain is for use in illustrative examples." [ref=e2]
- link "More information..." [ref=e3]
```

### Click an element
```bash
agent-browser --cdp "$CDP_ENDPOINT" click @e3
```

### Fill a form field
```bash
agent-browser --cdp "$CDP_ENDPOINT" fill @e5 "search query"
```

### Extract page content as markdown
```bash
agent-browser --cdp "$CDP_ENDPOINT" scrape
```

### Scroll
```bash
agent-browser --cdp "$CDP_ENDPOINT" scroll down
```

### Screenshot
```bash
agent-browser --cdp "$CDP_ENDPOINT" screenshot "$PWD/screens/page.png"
```

## Step 3: Close Session

```bash
integration.sh browser/session-close?sessionId=$SESSION_ID
```

## Workflow Pattern

항상 이 순서를 따르세요:

1. `open <url>` — 페이지 열기
2. `snapshot` — 현재 페이지 구조 확인 (ref ID 획득)
3. `click @ref` / `fill @ref "text"` — 인터랙션
4. `snapshot` — 변경된 페이지 확인
5. 필요시 2-4 반복
6. `scrape` — 최종 콘텐츠 추출
7. 세션 종료

## Limits

- 동시 세션: 1개
- 일일 세션: 50회
- 세션 최대 시간: 30분
- Idle 타임아웃: 5분

## Blocked Sites

보안상 다음 사이트는 접근 불가:
- 내부 네트워크 (10.x, 172.16-31.x, 192.168.x)
- 한국 금융기관 (KB, 신한, 우리, 하나, IBK, NH)
- 결제 프로세서 (이니시스, 토스페이먼츠, 카카오페이)

## Data Access Boundary

Browser automation is for rendering and interacting with websites. It is not a
general-purpose data acquisition channel for private, login-gated, rate-limited,
or high-volume platform data.

- Use official APIs or integration/provider connectors when the platform offers
  a structured data path.
- Ask for CSV/XLSX/exported data when the user needs filtering, enrichment, or
  reporting over large lists.
- Use the browser to inspect a flow, capture a page state, or perform bounded
  user-directed interactions. Do not promise long browser loops over thousands
  of records.
