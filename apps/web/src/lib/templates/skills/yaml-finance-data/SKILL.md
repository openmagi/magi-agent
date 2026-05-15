---
name: yaml-finance-data
description: Use when the user provides local YAML, JSON, CSV, or TSV financial datasets and wants them parsed, normalized, validated, compared, or converted into analysis-ready tables. This is a local-data skill, not a live market data connector.
---

# YAML Finance Data

Use this skill for offline or user-provided finance data files: company financials,
portfolio snapshots, valuation assumptions, factor tables, macro series, or
broker exports saved as YAML, JSON, CSV, or TSV.

## When to Use

- The user points to a local finance dataset in the workspace.
- The user uploads or pastes YAML/JSON/CSV financial data.
- The task is to normalize fields, compare periods, validate assumptions, or
  convert local data into Markdown, CSV, JSON, or analysis tables.
- The user asks for repeatable analysis from a saved dataset rather than live
  quote/API data.

## Not a Connector

This skill does not call an upstream finance API and does not require an API key.
If the user needs live or newly refreshed market data, use a connected data skill
instead, such as `yahoo-finance-data`, `fmp-financial-data`,
`alpha-vantage-finance`, `finnhub-market-data`, `fred-economic-data`,
`sec-edgar-research`, `imf-economic-data`, or `world-bank-data`.

## Workflow

1. Locate the file path or ask the user for the dataset if no file was provided.
2. Inspect the schema before calculating: periods, currency, units, tickers,
   fiscal calendar, source, and timestamp.
3. Parse with a structured parser when available. Prefer `node`, `ruby`, `jq`,
   or project tooling over ad hoc string splitting.
4. Normalize names and units before comparing rows:
   - Convert fiscal dates to ISO `YYYY-MM-DD`.
   - Preserve original currency and unit metadata.
   - Keep null/missing values distinct from zero.
5. Show assumptions and data quality issues before presenting conclusions.
6. Export transformed data only when the user asks for a file.

## Common Tasks

### Validate Period Coverage

Check that every ticker or segment has the same fiscal periods before computing
growth rates or margins.

### Normalize Statement Rows

Map aliases such as `revenue`, `sales`, and `total_revenue` to one canonical
field while preserving the original key in an audit column when needed.

### Compare Assumptions

For valuation input files, compare base, bear, and bull cases side by side and
flag assumptions that drive most of the output delta.

### Convert Formats

Convert YAML to CSV/JSON/Markdown tables after confirming the desired output
columns and row ordering.

## Output Rules

- Never imply the dataset is live or current unless the file metadata proves it.
- Cite the file path and timestamp/source metadata when available.
- If a field is missing, report it as missing rather than filling with zero.
- For investment conclusions, separate observed data from interpretation.
