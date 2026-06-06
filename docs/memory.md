# Memory

Memory gives the runtime durable context beyond one turn.

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
