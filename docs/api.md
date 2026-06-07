# API

API clients request work and inspect projections; the runtime owns authority.

The API should expose safe control and inspection surfaces while preserving runtime control over tools, evidence, approvals, memory, artifacts, and output projection.

## Runtime API boundary

API callers can submit tasks, answer approvals, inspect public projections, and fetch artifacts. They should not bypass ToolHost, validators, approval receipts, memory projection, or audit checkpoints.

For docs accuracy, distinguish cloud API calls from local source development
commands and component-level runtime APIs.
