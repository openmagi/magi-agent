---
name: financial-statement-forensics
description: "Use when performing deep financial statement forensics, quality-of-earnings review, accounting red-flag screening, restatement/auditor-change review, or disclosure anomaly analysis for listed companies. Supports KR DART and US SEC filings."
user_invocable: true
metadata:
  author: openmagi
  version: "1.0"
  category: accounting
---

# Financial Statement Forensics

상장사 재무제표를 단순 요약하지 않고, 손익·현금흐름·재무상태표·주석·공시 이벤트를 함께 읽어 이익 품질과 회계 리스크를 평가한다.

Use official accounting and finance terms only. Do not use proprietary metaphors or non-standard labels unless the user explicitly asks.

## When to Use

- "재무제표 포렌식", "분식 가능성", "회계 리스크", "QoE", "quality of earnings"
- 매출채권, 재고, 영업현금흐름, 자본화 비용, 손상차손, 특수관계자 거래가 의심될 때
- 감사인 변경, 정정공시, 계속기업 불확실성, 핵심감사사항(KAM), 세그먼트 재분류를 점검할 때
- `equity-financials` 결과보다 더 깊은 회계 해석이 필요할 때

## Required Inputs

| Input | Notes |
|-------|-------|
| Company | company name, ticker, or DART corp_code / SEC CIK |
| Market | KR or US |
| Period | latest annual preferred; quarterly allowed if annual unavailable |
| Basis | consolidated first; standalone only if consolidated unavailable |
| Depth | `quick` or `full`; default `full` for forensic requests |

If any required input is missing, ask once. Never guess the entity or reporting period.

## Data Sources

| Market | Primary | Supporting |
|--------|---------|------------|
| KR | `korean-corporate-disclosure` DART financials, filings, original document sections | audit report, footnotes, correction filings, IR materials, news |
| US | `sec-edgar-research` SEC 10-K/10-Q, XBRL companyfacts, filing document | notes, MD&A, auditor report, 8-K, proxy, earnings release |

Prefer source order: latest annual report -> latest quarterly report -> prior annuals for trend -> filings/events around accounting changes.

Useful open-source patterns from prior research:
- XBRL extraction/validation: Arelle, EdgarTools-style statement/fact handling.
- Bulk SEC data: SEC Financial Statement Data Sets and Financial Statement and Notes Data Sets.
- Formula references: Beneish M-Score, Dechow F-Score, Sloan accruals, Altman/Ohlson distress screens. Implement formulas transparently; do not rely on opaque scores.

## Workflow

### 1. Source Ledger

Create a source ledger before analysis:

| Item | Source | Date | Period | Notes |
|------|--------|------|--------|-------|
| Financial statements | 10-K / DART annual | YYYY-MM-DD | FY | consolidated |
| Notes / MD&A | filing section | YYYY-MM-DD | FY | sections read |
| Events | 8-K / DART list | YYYY-MM-DD | range | amendments, auditor changes |

Every material number in the report must tie to this ledger.

### 2. Statement Integrity

Validate the basics before interpreting:
- Balance sheet balances: assets = liabilities + equity.
- Cash flow ties: beginning cash + net cash flow = ending cash.
- Net income links: income statement net income reconciles to cash flow starting point where disclosed.
- Retained earnings roll-forward is directionally consistent with net income, dividends, buybacks, OCI, and other equity movements.
- Units, currency, fiscal year, consolidated/standalone basis are consistent.

If statements do not tie, stop and report the discrepancy first.

### 3. Earnings Quality

Compute and interpret over 3-5 years where data permits:

| Area | Checks |
|------|--------|
| Cash conversion | CFO / net income, FCF / net income, recurring negative CFO despite profit |
| Accruals | Sloan accruals, working-capital accruals, total accruals / assets |
| Revenue quality | DSO, receivables growth vs revenue growth, contract assets, deferred revenue |
| Inventory quality | DIO, inventory growth vs COGS/revenue, write-downs, gross margin pressure |
| Expense capitalization | capex vs D&A, capitalized R&D/software, intangible additions, impairment timing |
| One-offs | restructuring, asset sales, FX gains/losses, non-GAAP adjustments if disclosed |

Flag only with evidence. "Possible aggressive revenue recognition" is acceptable; "fraud" is not unless an official finding exists.

### 4. Balance Sheet and Disclosure Risk

Review:
- Cash, short-term investments, long-term financial assets, restricted cash.
- Interest-bearing debt, lease liabilities, off-balance-sheet commitments.
- Goodwill, intangibles, investment property, associates/JVs, fair value hierarchy.
- Related-party transactions, guarantees, contingencies, litigation, tax disputes.
- Auditor changes, qualified opinions, emphasis-of-matter paragraphs, going-concern language.
- Restatements, correction filings, late filings, segment reclassification, accounting policy changes.

For Korean companies, use DART correction and audit-related filings. For US companies, inspect 8-K Item 4.01, restatement language, and 10-K/10-Q note changes.

### 5. Forensic Screens

Run only when enough data exists. If inputs are missing, mark `N/A` and explain why.

| Screen | Use | Caveat |
|--------|-----|--------|
| Beneish M-Score | earnings manipulation risk screen | not a fraud conclusion; industry-sensitive |
| Dechow F-Score | misstatement risk screen | requires richer variables; often partial |
| Sloan accruals | accrual-heavy earnings quality | compare by industry |
| Altman Z / Ohlson O | distress risk | distress is not accounting manipulation |
| Piotroski F | financial strength context | not forensic alone |

Show formula inputs and year used. Do not output a score without the component table.

### 6. Synthesis

Classify each finding:

| Severity | Definition |
|----------|------------|
| Critical | official restatement, qualified/adverse opinion, going-concern warning, major unexplained tie-out failure |
| High | multiple reinforcing red flags across P&L, cash flow, balance sheet, and disclosure |
| Medium | one material red flag requiring follow-up |
| Low | monitor item or normal industry pattern |

## Output Format

```markdown
# Financial Statement Forensics - <Company> <Period>

## Executive Summary
- Overall risk: Low / Medium / High / Critical
- Highest-signal finding:
- Data confidence:

## Source Ledger
| Source | Filing date | Period | Sections used |

## Statement Integrity
| Check | Result | Evidence |

## Earnings Quality
| Metric | Y-4 | Y-3 | Y-2 | Y-1 | Current | Interpretation |

## Red Flags
| Severity | Area | Finding | Evidence | Follow-up |

## Forensic Screens
| Screen | Score | Inputs available | Interpretation |

## Disclosure Review
- Auditor / opinion:
- Restatements / corrections:
- Accounting policy changes:
- Related-party / contingency / impairment notes:

## Conclusion
<3-5 bullets with source-grounded implications>
```

## Routing

- If the user asks for valuation, market expectations, shareholder distributions, or reinvestment efficiency, also use `capital-allocation-quality`.
- If the user asks for a standard equity report, route through `equity-research` and include this skill as a deep-dive appendix.
- If the user asks for K-IFRS treatment, route to `accounting`.

## Hard Rules

- Do not fabricate numbers, pages, filing dates, analyst estimates, or management guidance.
- Use `N/A` with a reason when a metric cannot be computed.
- Prefer primary filings over market-data vendors.
- Distinguish evidence, inference, and speculation.
- Include "not investment advice" when output supports an investment decision.
