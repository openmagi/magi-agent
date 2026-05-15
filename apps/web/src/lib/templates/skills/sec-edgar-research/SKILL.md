---
name: sec-edgar-research
description: Use when researching US public company SEC filings, 10-K, 10-Q, 8-K reports, proxy statements, or insider trading data. Also use for US stock market company research.
---

# SEC EDGAR Research

SEC EDGAR is the US Securities and Exchange Commission's free database of corporate filings. No API key required.

## When to Use

- Looking up US public company financial filings (10-K, 10-Q, 8-K)
- Researching company fundamentals via XBRL data
- Tracking insider trading or institutional holdings
- Finding proxy statements or registration documents
- Comparing financials across companies

## API Endpoints

**Base URLs:**
- Full-text search: `https://efts.sec.gov/LATEST/search-index`
- Company data: `https://data.sec.gov`

**Required**: `User-Agent` header with contact info (SEC policy).

### 1. Full-Text Search

Search across all SEC filings by keyword.

```
web_fetch "https://efts.sec.gov/LATEST/search-index?q=%22artificial+intelligence%22&dateRange=custom&startdt=2025-01-01&enddt=2025-12-31&forms=10-K"
```

Parameters: `q` (query), `forms` (filing type), `dateRange`, `startdt`, `enddt`

### 2. Company Filings (by CIK)

Get all filings for a company. CIK is zero-padded to 10 digits.

```
web_fetch "https://data.sec.gov/submissions/CIK0000320193.json"
```

Response includes: `name`, `tickers`, `exchanges`, `filings.recent` (array of recent filings with `accessionNumber`, `form`, `filingDate`, `primaryDocument`)

### 3. Company Ticker → CIK Lookup

```
web_fetch "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=&CIK=AAPL&type=&dateb=&owner=include&count=10&search_text=&action=getcompany&output=atom"
```

Or use the company tickers JSON:
```
web_fetch "https://www.sec.gov/files/company_tickers.json"
```

### 4. XBRL Financial Data (Company Facts)

Get structured financial data (revenue, net income, assets, etc).

```
web_fetch "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
```

Response: `facts.us-gaap` object with keys like `Revenues`, `NetIncomeLoss`, `Assets` — each containing `units.USD` array with `val`, `end`, `fy`, `fp`, `form`.

### 5. Specific Filing Document

Build URL from submissions data: `https://www.sec.gov/Archives/edgar/data/{cik}/{accessionNumber}/{primaryDocument}`

## Workflow

1. **Find CIK**: Search by ticker or company name via company_tickers.json
2. **Get filings list**: Fetch `/submissions/CIK{padded_cik}.json`
3. **Find target filing**: Filter `filings.recent` by `form` (10-K, 10-Q, etc)
4. **Get document**: Build URL from `accessionNumber` + `primaryDocument`
5. **For financials**: Use XBRL companyfacts API instead of parsing documents

## Common Filing Types

| Form | Description |
|------|-------------|
| 10-K | Annual report (audited financials) |
| 10-Q | Quarterly report |
| 8-K | Current report (material events) |
| DEF 14A | Proxy statement |
| 4 | Insider trading |
| 13F-HR | Institutional holdings |
| S-1 | IPO registration |

## Red Flags

- Always include `User-Agent` header — SEC blocks requests without it
- CIK must be zero-padded to 10 digits (e.g., `0000320193` for Apple)
- Rate limit: max 10 requests per second per IP
- XBRL data may have different taxonomy versions — check `taxonomy` field
