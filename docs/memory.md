# Memory

Memory gives the runtime durable context beyond one turn.

## Enabling and disabling memory

A fresh install has memory **on by default**. When you run the installed CLI
(`magi` or `magi-agent serve`) a one-time bootstrap reads the optional
`[memory]` table from `~/.magi/config.toml`, overlays it on the install defaults
(`enabled = true`, `prefer_local_search = true`), and sets the matching
`MAGI_MEMORY_*` environment variables. With memory on the runtime writes a
concise daily entry per turn, builds the compaction tree, projects a memory
snapshot into the prompt, and injects a per-turn `<memory-recall>` block from the
local BM25 search backend. The zero-dependency PyBM25 backend is the default;
the qmd backend (and the global-collection auto-register opt-in) stays off unless
you turn it on explicitly.

To disable memory, either:

- set `[memory] enabled = false` in `~/.magi/config.toml`, or
- set the environment variable `MAGI_MEMORY_ENABLED=0`.

An explicit environment variable always wins over `config.toml`, which in turn
overrides the install defaults (precedence: env > config > install default). You
can disable just the per-turn recall (keeping writes/compaction) with
`[memory] prefer_local_search = false` or `MAGI_MEMORY_PREFER_LOCAL_SEARCH=0`.

This install-default-on behaviour comes from the CLI startup bootstrap only; the
code-level default (`resolve_memory_config()` with an empty env/config) stays
off, so library/test imports never silently activate memory.

## Memory vs Context

Context is what the model sees during a run. Memory is durable state that can be
recalled later. Keep them separate. A transcript line can be useful context
without becoming permanent memory.

## Good memory

Useful memory captures stable facts, preferences, decisions, project
constraints, and reusable context. It should be inspectable and correctable.

Examples:

- user preferences that remain valid across sessions;
- durable project constraints;
- decisions with dates and owners;
- reusable source references;
- known environment setup notes.

## Bad memory

Do not save every transcript line as durable memory. Avoid storing secrets,
temporary guesses, unsupported claims, or raw private payloads.

Also avoid storing:

- provider keys or auth tokens;
- unverified facts that should remain tied to source evidence;
- short-lived operational state;
- raw logs with private paths or customer data;
- hidden reasoning or provider payloads.

## Knowledge

Knowledge files and source material should remain distinguishable from memory.
When a factual answer depends on a source, keep the answer tied to source
evidence rather than a vague memory.

## Memory Receipts

A memory write should record public-safe evidence:

- what kind of memory was written;
- why it is durable;
- where it is stored;
- whether the user or policy allowed it;
- how it can be corrected or removed.

## Compaction

Transcript compaction should preserve task contracts, unresolved blockers,
artifact references, source evidence, and user-visible decisions. It should drop
noise, duplicates, hidden chain-of-thought style material, and private payloads
that are not needed for future work.
