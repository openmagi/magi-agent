---
name: tax-regulation-research
description: Use when looking up tax rules, IRS publications, tax forms, tax rates, or tax compliance requirements. Also use for tax planning research or cross-border tax questions.
---

# Tax Regulation Research

Access US tax regulations via IRS.gov and eCFR. For Korean tax law, use the korean-law-research skill (법제처 API covers 세법).

## When to Use

- Looking up US federal tax rules or IRS publications
- Finding specific IRC (Internal Revenue Code) sections
- Researching tax forms and instructions
- Understanding tax treaty provisions
- Cross-border tax planning research

## IRS Resources

### Tax Forms & Publications

IRS forms and publications are freely available as PDFs:

```
web_fetch "https://www.irs.gov/pub/irs-pdf/f1040.pdf"
web_fetch "https://www.irs.gov/pub/irs-pdf/p17.pdf"
```

URL pattern: `https://www.irs.gov/pub/irs-pdf/{filename}.pdf`

Common files:
| File | Description |
|------|-------------|
| f1040 | Form 1040 (Individual income tax) |
| f1120 | Form 1120 (Corporate income tax) |
| fw4 | Form W-4 (Withholding) |
| fw9 | Form W-9 (Taxpayer ID) |
| p17 | Publication 17 (Individual tax guide) |
| p334 | Publication 334 (Small business guide) |
| p519 | Publication 519 (Nonresident aliens) |
| p901 | Publication 901 (Tax treaties) |

### IRS Search

For finding specific tax topics:

```
web_search "site:irs.gov {tax topic}"
```

Example: `web_search "site:irs.gov foreign earned income exclusion 2025"`

## Internal Revenue Code (IRC)

### Via eCFR (Electronic Code of Federal Regulations)

Title 26 of the US Code contains all federal tax law. Access via eCFR:

```
web_fetch "https://www.ecfr.gov/api/versioner/v1/full/current/title-26.xml?part=1&section=1.61-1"
```

This returns the regulation text for a specific section. Parameters:
- `part`: CFR part number
- `section`: Section number (e.g., `1.61-1` for definition of gross income)

### Via US Code (uscode.house.gov)

```
web_search "site:uscode.house.gov title 26 section {number}"
```

## Key IRC Sections

| Section | Topic |
|---------|-------|
| 61 | Gross income defined |
| 162 | Trade or business deductions |
| 170 | Charitable contributions |
| 199A | Qualified business income deduction (pass-through) |
| 301-385 | Corporate distributions & reorganizations |
| 401-424 | Retirement plans (401k, IRA) |
| 451-461 | Timing of income/deductions |
| 871-898 | Nonresident aliens & foreign corporations |
| 901-909 | Foreign tax credit |
| 951-965 | Subpart F (controlled foreign corporations) |
| 1001-1092 | Capital gains & losses |

## Tax Treaty Research

US tax treaties are available from:

```
web_search "site:irs.gov tax treaty {country name}"
```

Or via Treasury: `web_search "site:treasury.gov tax treaty {country name}"`

## Korean Tax Law

Korean tax regulations are part of the Korean legal system. Use the **korean-law-research** skill to look up:
- 소득세법 (Income Tax Act)
- 법인세법 (Corporate Tax Act)
- 부가가치세법 (VAT Act)
- 상속세 및 증여세법 (Inheritance & Gift Tax Act)
- 국세기본법 (Framework Act on National Taxes)

## Workflow

1. **Identify topic**: Determine which IRC section or IRS publication applies
2. **Check IRS publication**: Start with the relevant publication for plain-language guidance
3. **Read actual code**: Look up IRC section via eCFR for the authoritative text
4. **Check regulations**: Treasury regulations (26 CFR) provide detailed interpretation
5. **Cross-border**: Check applicable tax treaty via IRS treaty table

## Red Flags

- Tax law changes frequently — always verify the effective date
- IRS publications are interpretive, not law — the IRC text controls
- State taxes are separate — this skill covers federal tax only
- This is reference material, not tax advice — users should consult a tax professional
