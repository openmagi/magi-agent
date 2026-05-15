---
name: equity-research
description: "Use when the user wants a full equity research report on a listed stock — thesis, BUY/HOLD/SELL, target price, valuation football field. Supports KR (KOSPI/KOSDAQ) and US (NYSE/NASDAQ) markets. Triggers: 'NVDA 분석', '삼성전자 리서치', 'stock research on Tesla', '종합 분석', '투자의견', 'equity report', 'research report', '목표가'."
user_invocable: true
metadata:
  author: openmagi
  version: "1.0"
  category: finance
---

# Equity Research — Orchestrator

상장주식 종합 리서치 리포트. 4개 서브스킬(`equity-business`, `equity-financials`, `equity-industry`, `equity-valuation`)을 orchestrate 하여 BUY/HOLD/SELL + 목표가 포함 리포트 산출.

**분석 도구일 뿐 투자자문 아님.** 리포트 말미에 disclaimer 포함.

회계 리스크, 이익품질, 정정공시, 감사인 변경, 비영업자산, 주주환원, CapEx 효율이 핵심 질문이면 `financial-statement-forensics` 또는 `capital-allocation-quality`를 추가 deep-dive로 호출한다.

## When to Use

- 상장주식 종합 분석 요청 ("엔비디아 분석해줘", "NVDA research")
- 목표가 / 투자의견 요청
- IC memo 스타일 요약
- 5분 스크리닝(`--depth=quick`) 또는 30분 풀 리포트(`--depth=full`)

**Do NOT use for:**
- 비상장 기업 가치평가 (다른 스킬 또는 범위 외)
- 암호화폐 (`crypto-market-data`)
- 단순 시세 조회 (`yahoo-finance-data` 직접 호출)

## Inputs

| Flag | Values | Default |
|------|--------|---------|
| `--ticker` | `NVDA`, `005930`, `"엔비디아"`, `"Tesla"` | 필수 |
| `--depth` | `quick` \| `full` | `quick` |
| `--peers` | 콤마 구분 티커 (optional, valuation용) | auto |

## Ticker Resolution

1. `^[A-Z]{1,5}$` → US (NYSE/NASDAQ) 가정
2. `^\d{6}(\.KS|\.KQ)?$` → KR, suffix 없으면 `yahoo-finance-data` quote API로 `.KS`/`.KQ` 판별
3. 한국어/영문 회사명 → web_search `"<name> ticker symbol"` → top result 티커 확인
4. 모호("현대" → 005380/000720/012330) → disambiguation 리스트 반환, **절대 guess 금지**

## Orchestration

### Quick Mode (`--depth=quick`, <5분)

순차 실행:
1. `equity-business --ticker <T> --depth=quick` → 사업 요약 5 bullets
2. `equity-financials --ticker <T> --depth=quick` → 최근 3Y 핵심 지표
3. `equity-valuation --ticker <T> --depth=quick` → trading multiples only
4. 본 스킬에서 thesis + recommendation 합성

### Full Mode (`--depth=full`, 20-30분)

`pipeline` 스킬로 병렬 spawn (batch limit 4):
- **Parallel group A**: `equity-business`, `equity-financials`, `equity-industry` (서로 독립)
- **Sequential**: A 완료 후 `equity-valuation` (financials 의존)
- **Converge**: 본 스킬에서 조합
- **Optional deep-dive**: `financial-statement-forensics` and/or `capital-allocation-quality` when requested or when `equity-financials` flags material QoE/capital-allocation issues

Full 모드는 `estimated_steps>=3` 이므로 AEF v6 SOUL.md가 자동 pipeline 강제함 — 본 스킬이 명시적으로 `pipeline` 호출하는 게 clean.

## Report Structure

```markdown
# <Company Name> (<TICKER>) — <BUY/HOLD/SELL>

**Date:** YYYY-MM-DD
**Current price:** $X | **Target:** $Y (±Z%)
**Sector:** <GICS/KSIC>
**Market cap:** $B
**52W range:** $low — $high

## Thesis (3 lines)
<핵심 투자 포인트 3줄>

## Business Summary
<equity-business 결과>

## Financial Summary
![](financial-trends.png)
<equity-financials 결과>

## Industry & Regulatory
<equity-industry 결과>

## Valuation
![](football-field.png)
<equity-valuation 결과>

## Catalysts & Risks
**Catalysts:** <3-5 bullets with timing>
**Risks:** <3-5 bullets with probability>

## Recommendation
- **View:** BUY / HOLD / SELL
- **Target:** $Y (method: DCF / comps / blended)
- **Time horizon:** 6-12M / 12-24M
- **Conviction:** High / Medium / Low

---
*This is research synthesis, not investment advice. Verify with primary sources.*
```

## Delivery

Channel auto-detect:
- **web/app**: `file-send.sh research-report.md` + 각 PNG
- **Telegram**: `telegram-file-output` v3 Section B

Markdown 안의 `![](filename.png)` 는 web chat renderer가 자동 inline.

## Error Handling

- Ticker 모호 → disambiguation 리스트 반환, 종료
- 데이터 누락 섹션 → `[DATA: unavailable — <reason>]` 로 표시, 리포트는 계속
- DCF 수렴 실패 → valuation에서 `[DCF: skipped]`, 멀티플 기반 목표가만 제시
- Peer 없음 → trading comps 스킵, annotate

**숫자 fabricate 절대 금지.** 모든 숫자는 출처(filing/quote API) 명시.

## Example

```bash
# Quick screening
equity-research --ticker NVDA --depth=quick

# Full report with custom peers
equity-research --ticker 005930 --depth=full --peers=000660,066570,005380

# Ambiguous → returns disambiguation
equity-research --ticker "현대"
# → "Multiple matches: 005380 현대차 / 000720 현대건설 / 012330 현대모비스. Which?"
```

## Disclaimers

- 리포트에 항상 포함: "This is research synthesis, not investment advice."
- 한국 채널의 경우: "본 자료는 투자권유가 아니며, 투자 판단의 책임은 이용자에게 있습니다."
- 가격·재무 데이터는 delayed, realtime 아님
