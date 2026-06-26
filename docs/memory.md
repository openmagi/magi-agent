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

## Search index (optional qmd, optional vectors)

Memory search has two backends behind one selector:

- **PyBM25** — pure-Python Okapi BM25, the zero-dependency default. It indexes
  `memory/**/*.md` plus top-level `MEMORY.md` / `ROOT.md` and ranks keyword
  matches. No install, sub-second, always available.
- **qmd** — the external `qmd` binary (the `@tobilu/qmd` npm package, or a
  Homebrew `qmd` formula), used when it is on `PATH` and `[memory] prefer_qmd`
  is true (the default). On the per-turn recall path it runs `qmd search`
  (BM25, no model load).

qmd is **not** a hard dependency — a fresh install searches memory fine without
it (the built-in BM25 backend). The qmd binary is also not installed for you; run
the one-time setup to enable the qmd index:

```
magi memory init              # install qmd + index this workspace (+embed by default)
magi memory init --no-vector  # keyword-only: install + index, skip the ~2GB embed
```

`init` installs the binary if missing (Homebrew, then `npm install -g
@tobilu/qmd`), registers this workspace's `memory/` tree as a private qmd
collection (so search is not silently empty), and writes the opt-ins to
`~/.magi/config.toml`. `magi doctor` shows whether qmd is present.

**Vector search defaults ON in config, but is explicit-only at runtime.** A
normal local install has `vector_search = true` (a "config-default-ON even if the
binary is OFF" setting). It only *arms* the capability: it does not install qmd
and does not run any embed by itself. Because the resolved default is ON,
`magi memory init` runs `qmd embed` (first run downloads an embedding model,
~2GB) unless you pass `--no-vector`; an explicit `[memory] vector_search = false`
(or `--no-vector`) opts out of the download. Even when on, semantic `qmd vsearch`
runs **only** on the explicit, latency-tolerant surfaces — `magi memory search
--vector` and the dashboard `/v1/app/memory/search?vector=1` endpoint — because
each vsearch invocation cold-loads the embedding model (~10-40s). The **per-turn
recall hot path always stays on BM25** regardless of `vector_search`, so turn
latency is never affected.

Search memory directly:

```
magi memory search "billing reconciliation"            # BM25 keyword
magi memory search "how do we reconcile credits" --vector   # semantic
```

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
