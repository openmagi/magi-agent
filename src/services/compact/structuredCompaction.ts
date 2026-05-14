/**
 * Structured Compaction Template (P4).
 *
 * Replaces the free-form compaction prompt with a 10-section forced
 * template to prevent summarization drift. Post-compaction validation
 * checks for required section headers and retries up to 2 times on
 * missing sections.
 *
 * Design reference: docs/plans/2026-05-11-context-intelligence.md §P4.
 */

export const REQUIRED_HEADERS = [
  "## 1. Active Intent",
  "## 2. Completed Steps",
  "## 3. Current Plan",
  "## 4. Modified Files",
  "## 5. Key Code Snippets",
  "## 6. Important Values",
  "## 7. Decisions Made",
  "## 8. Pending Questions",
  "## 9. Execution Contract State",
  "## 10. Next Immediate Step",
] as const;

export const MAX_COMPACTION_RETRIES = 2;

export const STRUCTURED_COMPACTION_PROMPT = `You are compacting a conversation transcript into a structured handoff memo.
A successor assistant will rely ONLY on this memo to continue work.

You MUST output ALL 10 sections below. Each section header is REQUIRED.
If a section has no content, write "None." — do NOT omit the header.

## 1. Active Intent
One sentence: what is the user trying to accomplish right now?

## 2. Completed Steps
Bulleted list of what has been done. Include tool names and outcomes.

## 3. Current Plan
Numbered steps remaining. If no explicit plan, infer from context.

## 4. Modified Files
REQUIRED: Every file path that was created, edited, or deleted.
Format: \`path/to/file.ts\` — action (created|modified|deleted)

## 5. Key Code Snippets
REQUIRED: Up to 5 code blocks that the successor needs to understand
the current state. Include function signatures, type definitions, or
config values that were discussed or changed.

## 6. Important Values
Numbers, IDs, URLs, hashes, versions, credentials (redacted), model
names, or any concrete value the successor might need. Table format.

## 7. Decisions Made
What was decided and why. Include rejected alternatives if discussed.

## 8. Pending Questions
Unresolved questions from the user or open design decisions.

## 9. Execution Contract State
If an execution contract is active, preserve it exactly:
goal, constraints, acceptance criteria, verification evidence, artifacts.
If none, write "No active execution contract."

## 10. Next Immediate Step
One sentence: what should the successor do first?`;

export interface CompactionValidation {
  valid: boolean;
  missing: string[];
}

export function validateCompactionOutput(summary: string): CompactionValidation {
  const missing = REQUIRED_HEADERS.filter((h) => !summary.includes(h));
  return { valid: missing.length === 0, missing };
}

export function buildRetryPrompt(missingSections: string[]): string {
  return [
    "Your previous compaction output was missing required sections.",
    "You MUST include ALL of the following section headers that were missing:",
    "",
    ...missingSections.map((s) => `- ${s}`),
    "",
    "Regenerate the complete 10-section handoff memo. Do NOT omit any section.",
    "If a section has no content, write \"None.\" under the header.",
  ].join("\n");
}
