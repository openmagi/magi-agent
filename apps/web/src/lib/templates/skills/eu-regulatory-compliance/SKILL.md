---
name: eu-regulatory-compliance
description: Use when researching EU regulations like GDPR, AI Act, Digital Markets Act, or other EU directives. Also use when checking EU compliance requirements or looking up EUR-Lex documents.
---

# EU Regulatory Compliance Research

EUR-Lex is the official portal for EU law. Free access, no API key required.

## When to Use

- Looking up EU regulations (GDPR, AI Act, DMA, DSA)
- Checking compliance requirements for EU markets
- Finding specific articles of EU directives
- Researching EU legislative history
- Understanding EU data protection rules

## Key EU Regulations Reference

| Regulation | CELEX Number | Key Topic |
|-----------|-------------|-----------|
| GDPR | 32016R0679 | Data protection & privacy |
| AI Act | 32024R1689 | AI regulation & risk classification |
| DMA (Digital Markets Act) | 32022R1925 | Platform competition rules |
| DSA (Digital Services Act) | 32022R2065 | Platform liability & content moderation |
| ePrivacy Directive | 32002L0058 | Electronic communications privacy |
| NIS2 Directive | 32022L2555 | Cybersecurity requirements |
| DORA | 32022R2554 | Financial sector digital resilience |
| Data Act | 32023R2854 | Data access & sharing rules |

## Looking Up Regulations

### Direct URL by CELEX Number

Each EU legal act has a CELEX number. Use it for direct access:

```
web_fetch "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679"
```

Format: `https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{celex_number}`

### Search by Keyword

```
web_search "site:eur-lex.europa.eu {regulation name} {topic}"
```

Example: `web_search "site:eur-lex.europa.eu GDPR Article 17 right to erasure"`

### Specific Article Lookup

For a specific article within a regulation:

```
web_fetch "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32016R0679"
```

Then search within the HTML for the article number (e.g., "Article 17").

## GDPR Quick Reference

| Article | Topic |
|---------|-------|
| Art. 5 | Data processing principles |
| Art. 6 | Lawful bases for processing |
| Art. 7 | Conditions for consent |
| Art. 12-14 | Transparency & information obligations |
| Art. 15 | Right of access |
| Art. 17 | Right to erasure |
| Art. 20 | Right to data portability |
| Art. 25 | Data protection by design |
| Art. 28 | Data processor obligations |
| Art. 32 | Security of processing |
| Art. 33-34 | Breach notification |
| Art. 44-49 | International data transfers |

## AI Act Risk Categories

| Risk Level | Examples | Requirements |
|------------|----------|-------------|
| Unacceptable | Social scoring, manipulative AI | Prohibited |
| High | Biometric ID, critical infrastructure, hiring | Conformity assessment, registration, monitoring |
| Limited | Chatbots, deepfakes | Transparency obligations |
| Minimal | Spam filters, AI games | No specific obligations |

## Workflow

1. **Identify regulation**: Use the reference table above for CELEX number
2. **Access full text**: Fetch via EUR-Lex URL with CELEX number
3. **Find specific article**: Search within document or use `web_search` with article number
4. **Check recitals**: Recitals (preamble) provide interpretive context for articles
5. **Cross-reference**: Check related implementing acts and delegated acts

## Red Flags

- EU regulations are directly applicable; directives require national implementation — check which type
- Always specify language (EN, DE, FR) in the URL — `/legal-content/EN/`
- Regulations may have transitional periods — check enforcement dates
- CELEX numbers: `3` = legislation, `6` = case law, `C` = EU treaties

## EUR-Lex API (via Platform Service)

Access EU legislation programmatically through `integration.sh` (routed to public-data-worker).

### Search EU Legislation

```bash
integration.sh "eurlex/search" '{"query": "artificial intelligence", "type": "regulation", "limit": 10}'
```

Parameters:
- `query` (required): keyword search
- `type`: `regulation`, `directive`, `decision`, `recommendation`
- `year`: filter by year (e.g., `2024`)
- `limit`: max results (default 10)

### Get Full Document by CELEX

```bash
integration.sh "eurlex/get" '{"celex": "32024R1689"}'
```

Returns: title, date, OJ reference, and full text in markdown.

### Get Specific Article

```bash
integration.sh "eurlex/article" '{"celex": "32016R0679", "article": 17}'
```

Returns: the text of the specified article.

### EUR-Lex Expert Search Reference

For advanced queries beyond keyword search, EUR-Lex also provides a SOAP webservice with SQL-like expert search syntax.

### Expert Search Query Language

The EUR-Lex expert search uses a SQL-like syntax:

```
SELECT DN, TI WHERE DN = 32016R0679
```

Fields:
- `DN` -- Document Number (CELEX)
- `TI` -- Title
- `TE` -- Text
- `AU` -- Author
- `DD` -- Date of Document
- `OJ` -- Official Journal Reference

Common queries:
```
# Find GDPR
SELECT DN, TI WHERE DN = 32016R0679

# Search by title keywords
SELECT DN, TI WHERE TI ~ "artificial intelligence" AND DD >= 2024-01-01

# Find all regulations in a year
SELECT DN, TI WHERE DN = 3* AND DD >= 2024-01-01 AND DD <= 2024-12-31
```

### Common Shortcuts

| Name | CELEX | Direct URL |
|------|-------|-----------|
| GDPR | 32016R0679 | `eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679` |
| AI Act | 32024R1689 | `eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R1689` |
| DSA | 32022R2065 | `eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32022R2065` |
| DMA | 32022R1925 | `eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32022R1925` |
| NIS2 | 32022L2555 | `eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32022L2555` |
| DORA | 32022R2554 | `eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32022R2554` |
| Data Act | 32023R2854 | `eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32023R2854` |
| ePrivacy | 32002L0058 | `eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32002L0058` |

### CELEX Number Format

Format: `{sector}{year}{type}{number}`
- Sector: `3` = legislation, `6` = case law, `C` = treaties
- Type: `R` = Regulation, `L` = Directive, `D` = Decision

## Cross-Reference Patterns for EU Law

When analyzing EU law, track these cross-references:
- **Implementing Acts**: Check for Commission implementing regulations (e.g., GDPR -> standard contractual clauses)
- **Delegated Acts**: Check for Commission delegated regulations
- **Recitals**: Recitals (preamble) are interpretive guides -- always cite recital number when using for interpretation
- **National Implementation**: For Directives, check member state transposition status

## Comparative Analysis Support

When comparing EU regulations with other jurisdictions, use the `/general-legal-research` skill's 10-axis comparative framework.

Key EU-specific comparison patterns:
- **EU vs US**: Regulation (EU) vs sector-specific (US); GDPR vs state privacy laws; AI Act vs proposed AI frameworks
- **EU vs Korea**: Data protection (GDPR vs 개인정보보호법); AI regulation (AI Act vs 인공지능법); digital platforms (DMA vs 플랫폼법)

## Source Grading (EU)

| Grade | Sources |
|-------|---------|
| A (primary) | EUR-Lex official texts, Official Journal of the EU, CJEU judgments |
| B (authoritative) | European Commission guidance, European Data Protection Board opinions, national DPA decisions |
| C (academic) | EU law journals, think tank reports, law firm analysis |
| D (unreliable) | Wikipedia, unattributed blog posts -- NEVER cite as sole basis |
