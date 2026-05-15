---
name: us-legal-research
description: Use when researching US federal or state laws, court cases, legal precedents, or federal regulations. Also use when looking up USC, CFR, or case law citations.
---

# US Legal Research

Access US laws and case law via CourtListener and GovInfo APIs. Both are free and public.

## When to Use

- Looking up US federal or state court opinions
- Searching for legal precedents on a topic
- Finding specific USC (US Code) or CFR (Code of Federal Regulations) sections
- Tracking federal legislation or congressional reports
- Researching federal register notices

## CourtListener API (Case Law)

**Base URL**: `https://www.courtlistener.com/api/rest/v4`

Free access. Optional API token for higher rate limits (register at courtlistener.com).

### Search Opinions

```
web_fetch "https://www.courtlistener.com/api/rest/v4/search/?q=artificial+intelligence+liability&type=o&order_by=score+desc"
```

Parameters:
- `q`: Search query
- `type`: `o` (opinions), `r` (RECAP/dockets), `oa` (oral arguments)
- `order_by`: `score desc` (relevance), `dateFiled desc` (newest)
- `court`: Filter by court (e.g., `scotus`, `ca9`, `nysd`)
- `filed_after` / `filed_before`: Date range (YYYY-MM-DD)

### Get Specific Opinion

```
web_fetch "https://www.courtlistener.com/api/rest/v4/opinions/{id}/"
```

### Search Courts

```
web_fetch "https://www.courtlistener.com/api/rest/v4/courts/?type=F"
```

Court types: `F` (federal), `S` (state), `FB` (federal bankruptcy)

### Common Court Codes

| Code | Court |
|------|-------|
| scotus | US Supreme Court |
| ca1-ca11 | Circuit Courts of Appeals |
| cadc | DC Circuit |
| cafc | Federal Circuit |
| nysd | Southern District of New York |
| cand | Northern District of California |

## GovInfo API (Federal Law & Regulations)

**Base URL**: `https://api.govinfo.gov`

Requires API key (free — register at api.data.gov).

### Search Collections

```
web_fetch "https://api.govinfo.gov/collections/USCODE/2024-01-01T00:00:00Z?offset=0&pageSize=10&api_key={key}"
```

Collections: `USCODE` (US Code), `CFR` (Code of Federal Regulations), `FR` (Federal Register), `BILLS` (Congressional Bills), `PLAW` (Public Laws)

### Search Published Documents

```
web_fetch "https://api.govinfo.gov/published/2024-01-01?offset=0&pageSize=10&collection=FR&api_key={key}"
```

### Get Document Summary

```
web_fetch "https://api.govinfo.gov/packages/{packageId}/summary?api_key={key}"
```

## Alternative: Direct USC/CFR Lookup

### US Code (uscode.house.gov)

```
web_search "site:uscode.house.gov title {number} section {number}"
```

Example: `web_search "site:uscode.house.gov title 17 section 107"` (Copyright fair use)

### eCFR (Electronic Code of Federal Regulations)

```
web_fetch "https://www.ecfr.gov/api/versioner/v1/full/current/title-{number}.xml?part={part}"
```

## Workflow

1. **Case law**: Use CourtListener to search opinions by topic and jurisdiction
2. **Federal statutes**: Search uscode.house.gov or GovInfo USCODE collection
3. **Federal regulations**: Use eCFR for current CFR text
4. **Recent changes**: Check Federal Register via GovInfo FR collection
5. **Citation research**: Use CourtListener's citation network to find related cases

## Red Flags

- Court opinions are primary law — legal commentary and summaries are secondary sources
- Always check if a case has been overruled or distinguished by later decisions
- Federal vs state law: USC/CFR are federal only; state law requires state-specific sources
- This is reference material, not legal advice — users should consult an attorney
