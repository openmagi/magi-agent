---
name: equity-valuation
description: "Use when valuing a listed equity — DCF, trading comps, SOTP, football field, scenario analysis. Supports KR/US. Triggers: 'DCF 모델', 'valuation', '목표가 계산', 'comps analysis', 'football field', 'SOTP', 'WACC', '시나리오 분석'. Typically called after equity-financials (needs historical data) but runs standalone."
user_invocable: true
metadata:
  author: openmagi
  version: "1.0"
  category: finance
---

# Equity Valuation

DCF + trading comps + SOTP (optional) + scenario + football field overlay.

## When to Use

- 목표가 산출 / 투자 판단
- DCF 민감도 분석
- 피어 그룹 멀티플 비교
- `equity-research` orchestrator 하위 호출 (마지막 단계)

## Inputs

| Flag | Values | Default |
|------|--------|---------|
| `--ticker` | 필수 |
| `--depth` | `quick` \| `full` | `quick` |
| `--peers` | 티커 콤마 (선택) | auto-select |
| `--wacc` | 숫자 (선택) | estimated |
| `--terminal-g` | 숫자 (선택) | 2-3% 시장 기본 |

## Data Sources

| Need | Source |
|------|--------|
| Historical financials | `equity-financials` output 재사용 또는 재조회 |
| Quote, market cap, shares outstanding | `yahoo-finance-data` |
| Risk-free rate | `fred-economic-data` 10Y treasury (US) / Firecrawl KTB (KR) |
| Beta | `yahoo-finance-data` summary |
| Peer multiples | `yahoo-finance-data` batch quote |
| M&A transaction comps | `firecrawl` news / Mergermarket public excerpts |

## Workflow

### Quick Mode (multiples only)

1. Peer set 자동 선택 (같은 GICS sub-industry top 3-5, `yahoo-finance-data`)
2. Peer EV/EBITDA, P/E, EV/Revenue 중앙값 + range
3. Target multiple × 대상 회사 LTM metric → implied value
4. 현재가 대비 upside/downside

출력: 3-line conclusion + 1 table. 차트는 football field 1개.

### Full Mode

**1. Trading comps**
- Peer set 구성 (사용자 지정 없으면 자동 8-12개)
- EV/EBITDA, EV/Revenue, P/E, P/B, P/S, EV/FCF 각 median + IQR
- 대상 회사의 각 멀티플 위치 (%ile)
- Implied value range

**2. Transaction comps** (해당 시)
- 최근 3Y 동종 업계 M&A deals (Firecrawl 검색 + 1차 출처 확인)
- Control premium 포함 multiples
- Implied value

**3. DCF**
- 5Y explicit forecast (revenue, margin, capex, WC 기반)
- Forecast 근거: `equity-financials` 추세 + 산업 CAGR
- Terminal: Gordon growth (`g`=2-3%) 또는 exit multiple
- WACC: `E/V × Re + D/V × Rd × (1-t)` — 각 component 명시
  - Re = Rf + β × ERP (ERP: 5-6% 기본, 시장별 조정)
  - Rd = LT debt effective rate from filings
- 민감도: WACC (x) × terminal g (y) 5×5 매트릭스
- 결과: enterprise value → net debt 제외 → equity value / shares = intrinsic value per share

**4. SOTP** (conglomerate 전용, `equity-business` segment revenue 있을 때만)
- 세그먼트별 peer multiple 적용
- Sum → less holdco discount (10-20%) → equity value

**5. Scenario**
| Scenario | Revenue CAGR | Margin | Multiple | Price |
|----------|-------------|--------|----------|-------|
| Bear | low | compressed | low end | $X |
| Base | expected | trend | median | $Y |
| Bull | high | expansion | high end | $Z |

**6. Football field**
- 각 방법 horizontal bar (range)
- 현재가 vertical line
- 52W range 별도 bar

## Charts

`football-field.png` (항상 생성):
```python
import matplotlib.pyplot as plt
methods = ['Trading comps (EV/EBITDA)', 'Trading comps (P/E)', 'DCF base', 'Transaction comps', '52W range']
lows = [...]
highs = [...]
fig, ax = plt.subplots(figsize=(9, 5))
ax.barh(methods, [h-l for h, l in zip(highs, lows)], left=lows, color='steelblue', alpha=0.7)
ax.axvline(current_price, color='red', linestyle='--', label=f'Current ${current_price}')
ax.set_xlabel('Implied price per share')
ax.legend()
fig.tight_layout()
fig.savefig('/tmp/equity-<ticker>-football-field.png', dpi=120)
```

`dcf-sensitivity.png` (Full mode만): WACC × terminal g heatmap.
```python
import numpy as np
import matplotlib.pyplot as plt
wacc_range = np.arange(0.07, 0.13, 0.01)
g_range = np.arange(0.01, 0.05, 0.01)
# Z[i,j] = intrinsic value at wacc[i], g[j]
fig, ax = plt.subplots(figsize=(7, 5))
im = ax.imshow(Z, cmap='RdYlGn', aspect='auto')
ax.set_xticks(range(len(g_range))); ax.set_xticklabels([f'{g:.0%}' for g in g_range])
ax.set_yticks(range(len(wacc_range))); ax.set_yticklabels([f'{w:.0%}' for w in wacc_range])
ax.set_xlabel('Terminal g'); ax.set_ylabel('WACC')
fig.colorbar(im, ax=ax)
fig.tight_layout()
fig.savefig('/tmp/equity-<ticker>-dcf-sensitivity.png', dpi=120)
```

## Output Format

```markdown
## Valuation

### Trading Comps
Peer set: <tickers>
| Multiple | Peer median | Peer range | Target LTM | Implied price |
|----------|-------------|-----------|-----------|---------------|
| EV/EBITDA | ...x | ...x — ...x | ... | $... |
| P/E | ...x | ...x — ...x | ... | $... |
| EV/Revenue | ...x | ...x — ...x | ... | $... |

### DCF (Full mode)
- **WACC:** ...% (Re=...%, Rd after-tax=...%)
- **Terminal g:** ...%
- **Intrinsic value:** $... per share

See `dcf-sensitivity.png`.

### Scenario
| Scenario | Price | vs current |
|----------|-------|-----------|
| Bear | $... | ...% |
| Base | $... | ...% |
| Bull | $... | ...% |

### Football Field
![](football-field.png)

### Blended Target
**$<target>** (method weights: DCF X%, comps Y%, transaction Z%)
Upside/downside: ...% vs current $<price>
```

## Error Handling

- Peer universe 없음 (unique business) → 멀티플 스킵, DCF에 의존
- DCF 음수 값 또는 비합리적 (>10x current) → `[DCF: output outside reasonable range, flagged]`
- WACC 추정 불가 (시장 beta 없음) → 업종 평균 beta 사용 + 명시
- Transaction comps 부재 → 섹션 스킵, "No comparable transactions found in 3Y window"

## Sanity Checks

- Revenue projection > 5Y 역사 max growth × 2 → flag
- Terminal g > WACC → 에러, terminal g 2.5% 기본으로 복귀
- Implied multiple (at target price) vs current peers > 3σ → flag overreach

## Example

```bash
equity-valuation --ticker NVDA --depth=full
equity-valuation --ticker 005930 --depth=quick --peers=000660,066570
```
