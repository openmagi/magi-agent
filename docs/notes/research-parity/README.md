# Research Parity Benchmark

This directory contains the local benchmark for measuring Magi research quality before changing runtime behavior.

Run the sample:

```bash
npm run research:eval
```

Evaluate a captured run:

```bash
node scripts/research-parity-eval.mjs \
  --benchmark docs/notes/research-parity/benchmark-v1.json \
  --run path/to/run.json \
  --out path/to/normalized-report.json
```

The evaluator does not call external models or web services. It normalizes scores supplied by a human evaluator or a future automated runner.

Rubric dimensions are scored from 0 to 5:

- factualAccuracy
- citationPrecision
- sourceFreshness
- primarySourcePreference
- contradictionHandling
- claimEvidenceCoverage
- toolUsageQuality
- permissionErgonomics
- synthesisUsefulness

Failure categories map to runtime work: `url_not_inspected` points at WebFetch and SourceLedger coverage, `uncited_claims` points at claim citation gates, and `parallel_research_gap` points at research-oriented child-agent presets.
