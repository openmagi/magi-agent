# API Stability Policy

Open Magi Agent is pre-1.0. This page states exactly which surfaces third-party
harness/recipe authors can build on today, and what we promise for each tier.

## Tiers

| Tier | Surfaces | Promise |
|---|---|---|
| **Stable-intent** | `EvidenceContract` / `EvidenceRequirement` / `EvidenceRecord` / `EvidenceSource` (`magi_agent.evidence.types`), the 17 builtin evidence types + `custom:PascalCaseName` namespace, `HarnessEngine.attach` + `EvidenceContractScope`, harness JSON schema (`docs/harness-schema.md`) | Breaking changes only at a minor release, with a CHANGELOG entry, a deprecation alias kept for ≥1 minor release, and a migration note in `docs/upgrading.md`. |
| **Evolving** | Recipe pack manifests (`recipes/`), tool manifests (`tools/manifest.py`), runtime env-flag names registered in `config/flags.py` | May change at any minor release; renames ship with a one-release alias when mechanically possible. Flag removals are listed in the CHANGELOG. |
| **Internal** | Everything else — `cli/engine.py`, `adk_bridge/`, `gates/`, `shadow/`, transport, prompt assembly | No stability promise. Import at your own risk; prefer filing an issue asking for a seam. |

## Practical guidance for harness authors

- Build against the **contract layer** (types + schema), not engine internals.
  Contracts are data; the engine honors the schema documented in
  `docs/harness-schema.md`.
- Pin a release. Between releases, `main` may change Internal surfaces freely.
- If your harness needs something only reachable through an Internal surface,
  open an issue titled `seam request:` — promoting a seam to Stable-intent is
  cheap before others depend on it, expensive after.

## Versioning of the harness schema

The harness JSON schema carries a `schemaVersion`. Readers must accept unknown
optional fields (additive evolution); removals or semantic changes bump the
major schema version and the loader keeps reading the previous major for ≥1
minor release.
