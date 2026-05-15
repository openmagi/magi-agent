---
name: equity-industry
description: "Use when analyzing a listed company's industry, regulatory environment, competitive landscape, or technology disruption risks. Supports KR/US. Triggers: '산업 분석', 'industry analysis', '경쟁 구도', 'regulatory risk', '규제 리스크', 'disruption', 'Porter 5 forces'. Typically called by equity-research but runs standalone."
user_invocable: true
metadata:
  author: openmagi
  version: "1.0"
  category: finance
---

# Equity Industry Analysis

상장사가 속한 산업 및 규제 환경 분석 — 성장성, 경쟁 구도, 규제 리스크, 기술 disruption, ESG/governance.

## When to Use

- "반도체 산업 분석" + specific ticker
- "NVDA 경쟁사 분석"
- "Tesla 규제 리스크"
- `equity-research` orchestrator 하위 호출

## Inputs

| Flag | Values | Default |
|------|--------|---------|
| `--ticker` | 필수 |
| `--depth` | `quick` \| `full` | `quick` |

## Data Sources

| Need | Skill |
|------|-------|
| Industry classification | `yahoo-finance-data` (GICS), DART `corp_cls` (KR) |
| Industry reports | `firecrawl` (Gartner, McKinsey, Statista public excerpts) |
| Regulatory — KR | `korean-law-research` (78 tools) |
| Regulatory — US | `us-legal-research` |
| Technology disruption signals | `sec-edgar-research` 8-K + news, `firecrawl` |
| ESG / governance signals | 10-K Item 9A, DART 감사보고서, 주주총회 자료 |

## Workflow

### Quick Mode

1. Sector / sub-industry (GICS for US, KSIC for KR)
2. Top 3-5 competitors + 대략 market share
3. 핵심 규제 1-2개 (현재 / 예정)
4. 주요 disruption risk 1개 (있으면)

### Full Mode

**Industry structure**
- GICS sub-industry 및 peer universe
- Industry size, growth (TAM + CAGR from public sources)
- Industry concentration (HHI if computable from top-N share)
- Cyclicality rating (High / Moderate / Low)

**Competitive analysis** (Porter-style, 간략)
- Rivalry (top 3-5 경쟁사 + share)
- Buyer power
- Supplier power
- New entrants
- Substitutes

**Regulatory environment**
- 해당 산업 핵심 regulations 3-5개 (country of incorporation)
- 최근 24개월 내 변경 / 예정
- Company-specific regulatory actions (SEC, DOJ, 공정위, 금융위 등)

**Technology disruption**
- 해당 업종 최근 disruption 사례 (기존 incumbent → 신흥 세력)
- 분석 대상 회사의 포지션 (disruptor / incumbent / neutral)

**ESG / governance red flags**
- 감사의견 (적정 / 한정 / 부적정 / 의견거절)
- 내부회계관리 중요한 취약점 disclosure
- CEO / CFO turnover (최근 3Y)
- Related-party 거래 비중
- 소송 exposure (10-K Item 3 / DART 소송 공시)

## Output Format

```markdown
## Industry & Regulatory

### Industry
- **Sector:** <GICS or KSIC>
- **Size:** $X (CAGR Y% est., source: <>)
- **Cyclicality:** <High/Moderate/Low>
- **Concentration:** HHI ~<N> (top-3 share ~<%>)

### Competitive Landscape
| Competitor | Ticker | Est. share | Note |
|------------|--------|-----------|------|
| ... | ... | ... | ... |

### Regulatory
- **Current:** ...
- **Pending:** ...
- **Company-specific:** ...

### Technology Disruption
- Position: <disruptor / incumbent / neutral>
- Key risk: ...

### ESG / Governance
- Audit opinion: ...
- Material weakness: Y/N
- CEO/CFO turnover: ...
- Related-party: ...
- Litigation exposure: ...
```

## Error Handling

- 산업 분류 unclear → "GICS undetermined, best-effort classification: <>"
- Competitor share 데이터 없음 → `[DATA: market share estimates unavailable]`
- 규제 변동 확신 없음 → 법령 링크만 제공, 해석은 유저 확인

## Example

```bash
equity-industry --ticker NVDA --depth=full
equity-industry --ticker 005930 --depth=quick
```

## Notes

차트 없음. 텍스트 중심 분석. 이 스킬에서 Firecrawl로 fetch한 법령/규제 요약은 반드시 원문 링크 병기.
