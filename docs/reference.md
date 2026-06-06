# Reference

Names and vocabulary used by the Magi Agent runtime docs.

Use this page for exact terms: policy snapshot, model-visible context, runtime-only evidence and claim state, ToolHost, validators, repair, projection, and audit.

## Runtime vocabulary

Policy snapshot: the effective tool, approval, evidence, repair, projection, and audit rules for a run.

Model-visible context: allowed input packet sent to the model.

Runtime-only evidence and claim state: ledgers, claim graphs, receipts, rejected claims, repair queues, and projection decisions withheld from the model unless safely summarized.

ToolHost / activity boundary: the execution boundary that turns approved proposals into receipts.

Governed output projection: the public-safe rendering of supported claims, artifacts, citations, warnings, and receipts.

append-only audit ledger: the durable record of policy snapshots, receipts, validator decisions, repair attempts, approvals, projections, and checkpoints.

## Install documentation guardrails

Document Homebrew install as the normal user path and source checkout as the development fallback.

Current docs may show git clone, npm install, npm run magi -- init, npm run magi -- doctor, and npm run magi -- start only as source fallback, provided `start` is described as the local docs/development server rather than live runtime activation.

Do not say the current cloud CLI installs or starts a local runtime.
