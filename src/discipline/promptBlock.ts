/**
 * <discipline> system-prompt block for the Coding Discipline subsystem
 * (docs/plans/2026-04-20-coding-discipline-design.md §"Layer 1 — Soft").
 *
 * Produces a small observation block the LLM can consult to self-
 * correct under pressure. The block is NOT a rule list (the coding-
 * agent skill carries that prose) — it's the live data the LLM tends
 * to forget: how many files have been touched, how long ago the last
 * commit was, what mode the classifier inferred.
 *
 * Injected by the disciplinePromptBlockHook on beforeLLMCall (priority
 * 6, after the classifier hook at 3 and the memory injector at 5).
 */

import type { Discipline } from "../Session.js";
import type { DisciplineSessionCounter } from "../hooks/builtin/disciplineHook.js";
import { CODING_SEMANTIC_NAVIGATION_POLICY } from "../prompt/RuntimePromptBlocks.js";

export interface DisciplinePromptInput {
  discipline: Discipline;
  counter: DisciplineSessionCounter;
  now: number;
}

/**
 * Human-friendly relative time label.
 *   0s → "just now"
 *   < 60s → "{n}s ago"
 *   < 60m → "{n}m ago"
 *   < 24h → "{n}h ago"
 *   else  → "{n}d ago"
 */
export function formatRelative(ms: number): string {
  if (ms < 1000) return "just now";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

/**
 * Build the `<discipline>` fenced block. Returns null when discipline
 * is fully off (tdd === false AND git === false AND requireCommit ===
 * "off") — callers must not prepend a null.
 */
export function buildDisciplineBlock(
  input: DisciplinePromptInput,
): string | null {
  const { discipline, counter, now } = input;
  if (!discipline.tdd && !discipline.git && discipline.requireCommit === "off") {
    return null;
  }
  const mode = discipline.lastClassifiedMode ?? "unknown";
  const lines: string[] = ["<discipline>"];
  lines.push(`Mode: ${mode}`);
  lines.push(`Source files modified this session: ${counter.sourceMutations}`);
  lines.push(`Test files modified this session: ${counter.testMutations}`);
  if (mode === "coding") {
    lines.push(
      'Coding workspace: for repo feature work, create or use CodeWorkspace under workspace/code/<project>/ or SpawnAgent workspace_policy="git_worktree" before writing source files.',
    );
    lines.push(
      "Commit units: use RepoTaskState to acquire the coding workspace lock, keep one commit unit in_progress, maintain the coding ledger, then record GitDiff/TestRun/CommitCheckpoint evidence before marking the unit completed.",
    );
    lines.push(
      "Do not edit source files directly in a dirty workspace root or parent checkout; keep repo files, tests, and generated outputs inside the dedicated coding workspace.",
    );
    lines.push(
      "Sandbox boundary: No Docker-in-Docker, privileged containers, root operations, or host Docker socket mounts; use in-workspace verification commands instead.",
    );
    lines.push(CODING_SEMANTIC_NAVIGATION_POLICY);
  }

  if (discipline.tdd) {
    const ratio =
      counter.sourceMutations === 0
        ? "n/a"
        : (counter.testMutations / counter.sourceMutations).toFixed(2);
    lines.push(`Ratio: ${ratio} (target >= 1.0 for TDD)`);
  }
  if (discipline.git) {
    if (counter.lastCommitAt) {
      const rel = formatRelative(now - counter.lastCommitAt);
      lines.push(
        `Last git commit: ${rel} (${counter.dirtyFilesSinceCommit} files ago)`,
      );
    } else {
      lines.push(
        `Last git commit: none (${counter.dirtyFilesSinceCommit} files dirty)`,
      );
    }
    if (
      counter.dirtyFilesSinceCommit >= discipline.maxChangesBeforeCommit
    ) {
      lines.push(
        `Suggestion: run tests and call CommitCheckpoint before continuing.`,
      );
    }
  }
  lines.push(`Enforcement: ${discipline.requireCommit}`);
  lines.push("</discipline>");
  return lines.join("\n");
}
