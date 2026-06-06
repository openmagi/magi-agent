# Memory

Memory is projected runtime state, not raw transcript storage. The memory subsystem (memory/ directory, 9 files) enforces read-only access with all writes blocked.

Magi Agent recalls information from previous sessions while keeping memory read-only by default for safety.

## Memory projection boundary

Magi Agent can recall information from previous sessions. Memory is read-only by default for safety — the agent can look up past decisions, notes, and facts, but cannot silently modify its own memory without explicit authorization.

Implementation: the memory write boundary governs all memory mutations. MemoryMutationIntent describes the proposed operation (remember, write, redact, delete, compact, decay, export) and produces a MemoryMutationReceipt with status blocked, approval_required, unsupported, or success. ALL writes are blocked by default. The memory contracts define MemoryRecord with scope (user/bot/org/project/session/task) and kind (event/note/fact/decision/preference/reasoning/artifact/relation). RecallRequest queries memory; RecallResult returns records with write access blocked by default.

- Use model-visible context for what the next model call may see.
- Use runtime-only evidence and claim state for verification records.
- Use projected memory for durable facts, decisions, preferences, and workflow state.
