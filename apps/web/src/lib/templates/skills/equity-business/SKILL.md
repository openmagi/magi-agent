---
name: equity-business
description: "Use when analyzing a listed company's business model, moat, customers, and TAM. Supports KR/US markets. Triggers: '사업 분석', 'business model', 'moat analysis', '경쟁 우위', 'TAM', '고객 집중도'. Typically called by equity-research orchestrator but can run standalone."
user_invocable: true
metadata:
  author: openmagi
  version: "1.0"
  category: finance
---

# Equity Business Analysis

상장사 사업 이해 — 비즈니스 모델, moat, 고객 집중도, TAM/SAM/SOM, unit economics.

## When to Use

- "NVDA 사업 구조 분석"
- "삼성전자 사업 부문별 매출"
- "Tesla moat 분석"
- `equity-research` orchestrator 의 하위 호출

## Inputs

| Flag | Values | Default |
|------|--------|---------|
| `--ticker` | `NVDA`, `005930`, `005930.KS` | 필수 |
| `--depth` | `quick` \| `full` | `quick` |

Ticker resolution: `equity-research` SKILL 참조 (동일 로직).

## Data Sources

| Market | Primary | Secondary |
|--------|---------|-----------|
| KR | `korean-corporate-disclosure` 사업보고서 Ⅰ편 (회사의 개요) + Ⅱ편 1-3 (사업의 내용) | `firecrawl` for recent IR deck / transcript |
| US | `sec-edgar-research` 10-K Item 1 (Business) + Item 7 (MD&A) | `firecrawl` Yahoo Finance business summary, Seeking Alpha transcript |

## Workflow

### Quick Mode (5 bullets)

1. 최근 사업보고서/10-K 1개 fetch
2. Business segments + 매출 비중 추출
3. Core product / service
4. Top 1-2 경쟁사
5. Moat 유형 (network / switching / scale / brand / regulatory) 한 줄

### Full Mode

1. **Company overview**: HQ, 설립연도, employees, CEO
2. **Business segments**: 세그먼트별 매출 / 영업이익 최근 3-5Y 추이 (chart 생성)
3. **Products & services**: 핵심 제품 라인, 최근 신제품
4. **Customer analysis**:
   - Top customers (10-K Item 1에서 disclosed 된 경우)
   - 고객 집중도 (top 10 비중)
   - B2B vs B2C 믹스
5. **Moat**: 각 타입별 평가 (Strong / Moderate / Weak / None)
6. **TAM / SAM / SOM**: best-effort, 명시된 출처 없을 땐 "est. from <source>"
7. **Unit economics**: 공시된 경우만 (ARPU, CAC, LTV, churn 등)
8. **Go-to-market**: 유통채널, 영업 조직 구조 (공시된 범위)

## Chart (Full mode only)

`revenue-breakdown.png` — 세그먼트별 매출 파이 또는 stacked bar (최근 3Y).

matplotlib 호출:
```python
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(8, 5))
# segments: list of {name, revenue_y3, revenue_y2, revenue_y1}
# stacked bar or pie
fig.savefig('/tmp/equity-<ticker>-revenue-breakdown.png', dpi=120, bbox_inches='tight')
```

파일 경로는 **반드시** `/tmp/equity-<TICKER>-revenue-breakdown.png` (orchestrator가 이름으로 찾음).

## Output Format

```markdown
## Business Summary

**<Company>** (<ticker>, <exchange>) — <sector> / <industry>
HQ: <city>, CEO: <name>, Employees: <N>

### Segments (LTM)
| Segment | Revenue | % of total | Growth YoY |
|---------|---------|-----------|------------|
| ... | ... | ... | ... |

### Moat
- **<Type>**: <Strong/Moderate/Weak> — <reason>

### Customer Concentration
<top customer / top N% / customer count range>

### TAM / SAM / SOM
- TAM: $X (source: <filing/report>)
- SAM: $Y
- SOM: $Z

### Key risks to business model
- ...
```

## Error Handling

- 사업보고서/10-K 없음 (신규 상장) → 증권신고서 또는 IR deck Firecrawl
- 세그먼트 공시 없음 → "Single segment (not disclosed)" 명시
- 데이터 누락 섹션 → `[DATA: unavailable — <reason>]`

## Example

```bash
equity-business --ticker NVDA --depth=full
equity-business --ticker 005930 --depth=quick
```
