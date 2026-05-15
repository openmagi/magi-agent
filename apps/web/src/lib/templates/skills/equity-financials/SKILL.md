---
name: equity-financials
description: "Use when analyzing a listed company's financials — revenue/margin trends, ROIC, FCF, leverage, QoE flags. Supports KR/US markets. Triggers: '재무 분석', 'financial analysis', 'ROIC', 'FCF', '현금흐름', '부채비율', 'EBITDA margin', 'quality of earnings'. Typically called by equity-research orchestrator but can run standalone."
user_invocable: true
metadata:
  author: openmagi
  version: "1.0"
  category: finance
---

# Equity Financials Analysis

상장사 재무 분석 — IS/BS/CF 추이, 핵심 비율, QoE red flag 스크리닝.

## When to Use

- "NVDA 재무 분석"
- "삼성전자 ROIC 추이"
- "Tesla 현금흐름 QoE 체크"
- `equity-research` orchestrator 하위 호출

## Deep-Dive Routing

- Use `financial-statement-forensics` when the user asks about forensic accounting, earnings manipulation risk, restatements, auditor changes, aggressive revenue recognition, or disclosure anomalies.
- Use `capital-allocation-quality` when the user asks whether cash, investments, buybacks, dividends, or CapEx translate into shareholder value.
- Keep this skill as the standard financial statement trend layer when the user only asks for ROIC, FCF, margins, or leverage.

Examples:
- "회계 리스크/재무제표 포렌식까지 봐줘" → route to `financial-statement-forensics`
- "현금/CapEx/주주환원 관점으로 봐줘" → route to `capital-allocation-quality`

## Inputs

| Flag | Values | Default |
|------|--------|---------|
| `--ticker` | 필수 |
| `--depth` | `quick` \| `full` | `quick` |
| `--years` | 정수, 1-10 | quick=3, full=5 |

## Data Sources

| Market | Source | Method |
|--------|--------|--------|
| KR | `korean-corporate-disclosure` | `integration.sh "dart/fnlttSinglAcntAll?corp_code=<code>&bsns_year=YYYY&reprt_code=11011&fs_div=CFS"` (연결 우선) |
| US | `sec-edgar-research` | XBRL facts via `https://data.sec.gov/api/xbrl/companyfacts/CIK<10자리>.json` |
| Both | `yahoo-finance-data` | 시세, 시가총액, TTM 멀티플 |

연결(CFS) 우선, 없으면 개별(OFS).

## Workflow

### Quick Mode

최근 3Y 데이터 추출:
1. Revenue, gross margin, operating margin, net margin
2. ROIC, ROE (estimation 허용 시 명시)
3. Net debt / EBITDA
4. FCF = OCF − capex
5. 1-2 red flag 체크 (DSO 급증, 재고 급증, 감사인 변경)

출력: 지표 table + 핵심 takeaway 3줄. 차트 없음.

### Full Mode

5Y 상세 분석:

**Income statement**
- Revenue, COGS, GP, operating expenses breakdown, operating income, non-operating, tax, net income
- Segment IS (공시 시)
- Growth rates (YoY), CAGR

**Balance sheet**
- Current assets / liabilities
- Cash + ST investments
- AR / inventory / goodwill / intangibles
- LT debt / lease liabilities
- Equity components

**Cash flow**
- OCF, ICF, FCF, capex intensity, working capital change
- FCF conversion (FCF / NI)
- Capex vs D&A (maintenance vs growth 구분 시도)

**Ratios & health**
- ROIC, ROE, ROA
- Gross/operating/net margin
- Net debt / EBITDA, interest coverage
- Altman Z (US manufacturing 가중치, service 업종이면 skip)
- Working capital: DSO, DIO, DPO, CCC

**QoE red flags** (하나라도 해당 시 리포트에 명시):
- DSO 급증 (10+ days YoY)
- 재고 증가율 > 매출 증가율 × 1.5
- Cash conversion <0.8 지속
- Related-party 거래 비중 증가
- 감사인 변경
- Segment reporting 재분류
- Goodwill 미상각 상태에서 impairment 지연 신호

## Chart (Full mode)

`financial-trends.png` — 3-panel stacked:
1. 매출 + YoY 성장률 (dual axis)
2. Margin trends (gross / operating / net)
3. FCF + FCF conversion

```python
import matplotlib.pyplot as plt
fig, axes = plt.subplots(3, 1, figsize=(9, 10), sharex=True)
# ... (panel 1: bar + line)
# ... (panel 2: 3 lines)
# ... (panel 3: bar + line)
fig.tight_layout()
fig.savefig('/tmp/equity-<ticker>-financial-trends.png', dpi=120)
```

## Output Format

```markdown
## Financial Summary

### Key Metrics (<N>Y)
| Metric | Y-4 | Y-3 | Y-2 | Y-1 | LTM |
|--------|-----|-----|-----|-----|-----|
| Revenue ($M) | ... | ... | ... | ... | ... |
| Gross margin | ...% | ...% | ...% | ...% | ...% |
| Op margin | ...% | ...% | ...% | ...% | ...% |
| Net income ($M) | ... | ... | ... | ... | ... |
| FCF ($M) | ... | ... | ... | ... | ... |
| ROIC | ...% | ...% | ...% | ...% | ...% |
| Net debt / EBITDA | ... | ... | ... | ... | ... |

### Cash Flow Quality
FCF conversion (FCF/NI): ...
Capex intensity: ...% of revenue
Working capital change impact: $...M

### QoE Red Flags
- [✓/✗] DSO trend
- [✓/✗] Inventory growth
- [✓/✗] Cash conversion
- [✓/✗] Segment reclassification
- [✓/✗] Auditor change

### Takeaways
1. ...
2. ...
3. ...
```

## Error Handling

- XBRL 개념 mismatch (IFRS ↔ GAAP) → `accounting` 스킬로 bridge hint
- Segment data 없음 → "Segment reporting not disclosed"
- 감사보고서 unavailable → 10-K footnote 또는 DART attached PDF 파싱 (`pdf-extract-robust`)
- 숫자 cross-verify: 최소 2 출처 일치 시에만 리포트 기재, 불일치 시 `[CONFLICT: source A=X, source B=Y]`

## Example

```bash
equity-financials --ticker NVDA --depth=full --years=5
equity-financials --ticker 005930 --depth=quick
```
