---
name: DatabaseQuery
permission: net
description: "Execute read-only SQL queries against PostgreSQL"
version: "0.1.0"
dangerous: true
---

# DatabaseQuery

Execute read-only SQL queries against a PostgreSQL database. This tool
is marked as `dangerous: true`, which means the runtime will request
explicit user consent before executing it.

DDL and DML statements (DROP, DELETE, INSERT, UPDATE, etc.) are
blocked by the `validate()` function.

## Setup

Set the `DATABASE_URL` environment variable or pass `connectionString`
as input. Requires a PostgreSQL client library (e.g. `pg`).
