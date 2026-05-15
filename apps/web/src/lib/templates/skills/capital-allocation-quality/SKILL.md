---
name: capital-allocation-quality
description: "Use when analyzing listed-company capital allocation quality, non-operating assets, shareholder distributions, reinvestment discipline, growth capex, owner-return proxies, and market-implied growth expectations."
user_invocable: true
metadata:
  author: openmagi
  version: "1.0"
  category: finance
---

# Capital Allocation Quality

상장사의 회계 수치를 주주 관점에서 재해석한다. 영업에 필요한 자산과 비영업 금융자산을 분리하고, 배당·자사주·재투자·CapEx가 장기 주주가치에 어떤 영향을 주는지 평가한다.

Use official terminology: operating assets, non-operating assets, excess cash, interest-bearing debt, shareholder distributions, net buybacks, invested capital, maintenance capex, growth capex, ROIC, FCF. Do not use proprietary metaphors or non-standard labels in final output unless the user explicitly requests them.

## When to Use

- "주주환원 포함해서 평가", "현금 많은 회사 평가", "CapEx가 낭비인지 투자자인지"
- "시총에서 현금 빼고 보면 수익률이 얼마냐", "비영업자산 반영한 밸류에이션"
- `financial-statement-forensics` 후 자본배분 관점의 결론이 필요할 때
- 투자 리서치에서 FCF/ROIC만으로 설명되지 않는 대규모 재투자를 해석할 때

## Required Inputs

| Input | Notes |
|-------|-------|
| Company | company name, ticker, or DART corp_code / SEC CIK |
| Market | KR or US |
| Period | latest annual preferred |
| Years | default 5Y |
| Market cap date | latest close or fiscal year-end close; disclose which one |

Ask once for missing inputs. If current market cap is needed, fetch current quote with source and date.

## Data Sources

| Item | Source |
|------|--------|
| Financial statements | KR DART / US SEC |
| Market cap and price | Yahoo/FMP/web-search; cite date |
| Dividends and buybacks | cash flow statement, equity note, treasury stock filings, 10-K/10-Q |
| CapEx and PPE roll-forward | cash flow statement, PPE note, MD&A, IR deck |
| Strategic projects | notes, MD&A, DART material filings, 8-K, IR, credible news |
| Risk-free rate / ERP / sector yield | current web search when market-implied expectations are requested |

## Core Analysis

### 1. Separate Operating and Non-Operating Capital

Classify balance-sheet items conservatively:

| Bucket | Include | Exclude / Adjust |
|--------|---------|------------------|
| Operating assets | PP&E, inventory, operating receivables, right-of-use assets, core intangibles | idle assets if separately disclosed |
| Non-operating assets | excess cash, short/long-term financial investments, non-core investment property, non-core equity investments | restricted cash, pledged assets, minimum operating liquidity |
| Financial obligations | interest-bearing debt, finance lease liabilities, non-operating payables | operating payables used in working capital |

For banks, insurers, brokers, REITs, and leasing companies, do not mechanically separate financial assets as non-operating. Their financial assets and liabilities are often operating capital.

### 2. Estimate Net Non-Operating Financial Position

Use a transparent bridge:

```text
Net non-operating financial position
= non-operating cash and financial assets
- restricted/minimum operating liquidity adjustments
- interest-bearing debt
- non-operating financial liabilities
- estimated tax/friction on asset realization when material
```

If minimum operating cash cannot be estimated, show both unadjusted and conservative adjusted cases.

### 3. Shareholder Value Accretion Proxy

Use this as an analytical proxy, not a GAAP measure:

```text
Annual shareholder value accretion proxy
= increase in net non-operating financial position
+ dividends paid
+ net buybacks
```

Net buybacks = share repurchases minus dilution from stock compensation, option exercises, convertibles, or new issuance where disclosed.

Then compare:

```text
Market-implied operating asset value
= market capitalization - net non-operating financial position

Capital-allocation yield
= annual shareholder value accretion proxy / market-implied operating asset value
```

If the denominator is zero or negative, do not force a yield. Explain the balance-sheet implication.

### 4. Reinvestment-Adjusted Diagnostic

Growth investment can reduce current distributions while increasing future earning power. Use a diagnostic, not a valuation fact:

```text
Net PP&E increase = current PP&E - prior PP&E
Reinvestment-adjusted proxy = shareholder value accretion proxy + positive net PP&E increase
Reinvestment-adjusted yield = reinvestment-adjusted proxy / market-implied operating asset value
```

Limitations:
- Net PP&E change is not equal to growth capex.
- Adjust for depreciation, acquisitions, disposals, FX translation, revaluation, right-of-use assets, and construction-in-progress if disclosed.
- If net PP&E increase is <= 0, state "reinvestment adjustment excluded: no positive net PP&E increase."

### 5. Strategic CapEx Assessment

Only calculate ROI, IRR, NPV, EBITDA contribution, or utilization scenarios when source-backed assumptions exist. Otherwise use `N/A` and explain missing inputs.

Required review:
- CapEx / revenue and CapEx / depreciation.
- PP&E roll-forward and construction-in-progress.
- Management's stated project purpose, expected completion, target capacity, and budget.
- Funding source: operating cash, debt, equity, asset sale.
- Early evidence: revenue growth, margin improvement, capacity utilization, order backlog.

Classify:

| Judgment | Evidence |
|----------|----------|
| Maintenance-heavy | CapEx roughly tracks D&A; no stated expansion; stable capacity |
| Strategic reinvestment | major capacity/R&D/productivity project with disclosed rationale |
| Potential overinvestment | capex rises while utilization, margin, backlog, or demand deteriorates |
| Insufficient evidence | project economics not disclosed |

### 6. Market-Implied Expectations

Run only when the user asks for market expectations or when the capital-allocation yield is weak and valuation depends on future growth.

Fetch current external inputs with dates:
- local 10Y government bond yield or US 10Y Treasury for USD analysis,
- current equity risk premium from a recognized source,
- sector or dividend-stock yield benchmark.

```text
Required return = max(risk-free rate + ERP, sector dividend yield benchmark, 8% fallback)
Required growth multiple = required return / reinvestment-adjusted yield
Implied CAGR = required growth multiple^(1/n) - 1
```

Calculate for 5Y, 7Y, and 10Y. If reinvestment-adjusted yield is <= 0, do not compute CAGR; explain that current accounting returns do not support the market price without a turnaround assumption.

## Output Format

```markdown
# Capital Allocation Quality - <Company> <Period>

## Executive Summary
- Market capitalization:
- Net non-operating financial position:
- Shareholder distributions:
- Capital-allocation yield:
- Reinvestment-adjusted yield:
- Main conclusion:

## Methodology
<brief explanation of operating/non-operating classification and proxies>

## Company Overview
<2-3 paragraphs from filings and MD&A>

## Four-Step Financial Review
### Step 1 - Prior-Period Balance Sheet
### Step 2 - Current-Period P&L
### Step 3 - Equity Movements and Shareholder Distributions
### Step 4 - Current-Period Balance Sheet
### P&L to Balance Sheet Bridge

## Non-Operating Asset and Financial Obligation Bridge
| Item | Prior | Current | Change | Source |

## Capital Allocation Yield
| Year | Market cap basis | Net non-operating position | Value accretion proxy | Yield |

## Strategic Reinvestment Review
| Project / item | Evidence | Amount | Timing | Expected impact | Confidence |

## Reinvestment-Adjusted Diagnostic
| Year | Value accretion proxy | Net PP&E increase | Adjusted proxy | Adjusted yield |

## Market-Implied Expectations
<include only when requested or needed; cite live inputs>

## Conclusion and Risks
- Highlights
- Risks
- Data limitations
```

## Quality Gates

- Every number must have a source, period, and unit.
- Do not present analytical proxies as GAAP or K-IFRS measures.
- Do not call all cash distributable; restricted cash and minimum operating liquidity must be considered.
- Do not classify all debt as non-operating for financial institutions.
- Do not treat net PP&E increase as growth capex without caveats.
- If source-backed project economics are unavailable, output `N/A` instead of fabricating ROI/IRR/NPV.
- Always include an investment-research disclaimer when making valuation implications.

## Routing

- Use `financial-statement-forensics` first when accounting quality or manipulation risk is the main question.
- Use `equity-financials` first when the user only wants standard ROIC, FCF, margin, leverage, or trend analysis.
- Use `equity-valuation` after this skill if the user asks for target price or DCF.
