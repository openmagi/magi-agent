# Heartbeat Protocol

## Output Isolation (CRITICAL — read first)
**Any text you output WILL be delivered to the user's Telegram/chat.**
There is NO filtering between your response and the user's channel.

- Your ONLY permitted output is `__SILENT__` (unless you have a user-facing scheduled action)
- NEVER output: status reports, error messages, recovery logs, tool failure traces, "checking...", "done", debug info
- If a tool call fails: log the error to SCRATCHPAD.md via file.edit, then respond `__SILENT__`
- If maintenance succeeds: respond `__SILENT__` — do NOT narrate what you did
- If maintenance fails: log to SCRATCHPAD.md, respond `__SILENT__`
- Think of it this way: **you are in a silent background thread, not a conversation**

## Runtime-Owned Maintenance
- Hipocampus checkpointing, compaction cadence, recall injection, transport retry, output-purity filtering, and cron delivery safety are runtime-owned.
- Heartbeat should not narrate these mechanisms or re-run a full session-boot checklist.

## On Heartbeat Trigger
1. Check `SCRATCHPAD.md`, `plans/`, and `TASK-QUEUE.md` for pending scheduled work.
2. Run only lightweight maintenance that this heartbeat explicitly owns.
3. If search or KB metadata is stale, use the available refresh path and record the result in `SCRATCHPAD.md`.
4. If a scheduled user-facing action is due, execute it with the normal delivery path and then return to silence.
5. Otherwise respond `__SILENT__`.

## Rules
- Default response: `__SILENT__` — nothing else
- The ONLY exception: a scheduled task in TASK-QUEUE that explicitly requires sending a message to the user (e.g., channel-posting skill)
- No greetings, no explanations, no status reports, no "all clear" messages
- No error messages, no tool failure output, no recovery narratives
- If you encounter ANY error during heartbeat operations, write it to SCRATCHPAD.md and respond `__SILENT__`

## Memory Maintenance
- Hipocampus handles checkpointing, compaction, and recall automatically.
- Do not force manual compaction from heartbeat unless an operator explicitly asks for maintenance.
