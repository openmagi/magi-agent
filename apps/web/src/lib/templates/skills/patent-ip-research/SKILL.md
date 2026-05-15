---
name: patent-ip-research
description: Use when searching patents, checking prior art, analyzing patent landscapes, or looking up trademarks. Also use for intellectual property research or freedom-to-operate analysis.
---

# Patent & IP Research

USPTO PatentsView provides free access to US patent data. No API key required.

## When to Use

- Searching for patents by keyword, inventor, or assignee
- Prior art investigation
- Patent landscape analysis (by CPC class or technology area)
- Freedom-to-operate research
- Tracking patent portfolios of companies

## API Endpoints

**Base URL**: `https://api.patentsview.org`

All queries use POST with JSON body. Response is JSON.

### 1. Patent Search

```
system.run ["curl", "-s", "-X", "POST", "https://api.patentsview.org/patents/query", "-H", "Content-Type: application/json", "-d", "{\"q\":{\"_and\":[{\"_text_any\":{\"patent_abstract\":\"machine learning\"}},{\"_gte\":{\"patent_date\":\"2024-01-01\"}}]},\"f\":[\"patent_number\",\"patent_title\",\"patent_date\",\"patent_abstract\",\"assignee_organization\"],\"o\":{\"per_page\":10}}"]
```

### 2. Inventor Search

```
system.run ["curl", "-s", "-X", "POST", "https://api.patentsview.org/inventors/query", "-H", "Content-Type: application/json", "-d", "{\"q\":{\"inventor_last_name\":\"Hinton\"},\"f\":[\"inventor_first_name\",\"inventor_last_name\",\"patent_number\",\"assignee_organization\"],\"o\":{\"per_page\":10}}"]
```

### 3. Assignee (Company) Search

```
system.run ["curl", "-s", "-X", "POST", "https://api.patentsview.org/assignees/query", "-H", "Content-Type: application/json", "-d", "{\"q\":{\"_contains\":{\"assignee_organization\":\"Google\"}},\"f\":[\"assignee_organization\",\"patent_count\",\"assignee_id\"],\"o\":{\"per_page\":10}}"]
```

### 4. CPC Classification Search

Find patents by Cooperative Patent Classification code.

```
system.run ["curl", "-s", "-X", "POST", "https://api.patentsview.org/patents/query", "-H", "Content-Type: application/json", "-d", "{\"q\":{\"_begins\":{\"cpc_subgroup_id\":\"G06N3\"}},\"f\":[\"patent_number\",\"patent_title\",\"patent_date\",\"cpc_subgroup_id\"],\"o\":{\"per_page\":10}}"]
```

## Query Syntax

| Operator | Example | Description |
|----------|---------|-------------|
| `_text_any` | `{"_text_any":{"patent_abstract":"AI"}}` | Full-text search |
| `_text_all` | `{"_text_all":{"patent_title":"neural network"}}` | All words must appear |
| `_eq` | `{"_eq":{"assignee_organization":"Apple Inc."}}` | Exact match |
| `_contains` | `{"_contains":{"patent_title":"battery"}}` | Substring match |
| `_begins` | `{"_begins":{"cpc_subgroup_id":"H01M"}}` | Starts with |
| `_gte` / `_lte` | `{"_gte":{"patent_date":"2024-01-01"}}` | Date/number range |
| `_and` / `_or` | `{"_and":[{...},{...}]}` | Combine conditions |

## Common CPC Classes

| Code | Technology Area |
|------|----------------|
| G06N | Computing - Machine learning, AI |
| G06F | Computing - General |
| H04L | Network/Communication protocols |
| A61K | Pharmaceuticals |
| H01M | Batteries/Fuel cells |
| G16H | Healthcare informatics |
| B60L | Electric vehicles |

## Workflow

1. **Keyword search**: Start with `_text_any` on `patent_abstract` or `patent_title`
2. **Narrow by date**: Add `_gte`/`_lte` on `patent_date`
3. **Filter by assignee**: Add `assignee_organization` filter
4. **Analyze CPC classes**: Check `cpc_subgroup_id` in results to understand technology classification
5. **Deep dive**: Use patent numbers to look up full text on Google Patents

## Red Flags

- Rate limit applies — avoid rapid-fire queries
- Data covers US patents only (for international, use Google Patents or Espacenet)
- Patent abstracts may use specialized legal language — search multiple synonym variations
- `_text_any` is OR logic, `_text_all` is AND logic
