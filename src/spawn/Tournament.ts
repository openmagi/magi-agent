/**
 * Tournament — T3-16 OMC Port A orchestration.
 *
 * Extracted from tools/SpawnAgent.ts (R4 refactor, 2026-04-19). Owns the
 * tournament phase: fan out N child-variant runs, score each, rank by
 * score, optionally clean up losers, and emit the `tournament_result`
 * AgentEvent. This module is intentionally decoupled from the
 * Tool/Agent types — callers pass in an opaque `runChild` + `scoreChild`
 * closure so Tournament stays composable and easy to unit-test.
 *
 * Invariants preserved from the original SpawnAgent implementation:
 *   • spawn dirs laid out as `.spawn/{parentTurnId}.tournament-{n}/`
 *     under the parent workspaceRoot; prepared up front in Phase 1 so
 *     callers can assert the filesystem layout.
 *   • concurrency defaults to `variants`, capped by
 *     TOURNAMENT_MAX_CONCURRENCY (5). `concurrency=1` yields strict
 *     sequential execution (test t6 relies on this for deterministic
 *     ordering).
 *   • Ranking: score DESC, ties broken by variantIndex ASC.
 *   • Scorer failures absorbed: thrown / NaN / missing score → score=0
 *     with an audit breadcrumb via `stageAuditEvent`.
 */

import fs from "node:fs/promises";
import path from "node:path";
import { Workspace } from "../storage/Workspace.js";

export const TOURNAMENT_MIN_VARIANTS = 2;
export const TOURNAMENT_MAX_VARIANTS = 5;
export const TOURNAMENT_MAX_CONCURRENCY = 5;

export interface TournamentVariantResult {
  variantIndex: number;
  score: number;
  finalText: string;
  spawnDir: string;
}

export interface TournamentResult {
  mode: "tournament";
  winnerIndex: number;
  variants: TournamentVariantResult[];
  winnerWorktreeApply?: {
    action: "preview";
    spawnDir: string;
  };
}

export interface PreparedVariant {
  variantIndex: number;
  taskId: string;
  spawnDir: string;
  spawnWorkspace: Workspace;
}

/**
 * Injected surface of the parent tool context needed by Tournament.
 * Mirrors the shape of the subset of `ToolContext` we actually use —
 * lets tests construct a minimal stub without pulling in the full Tool
 * wiring.
 */
export interface TournamentContext {
  workspaceRoot: string;
  turnId: string;
  stageAuditEvent: (event: string, data?: Record<string, unknown>) => void;
  emitAgentEvent?: (event: unknown) => void;
}

export interface RunTournamentOptions {
  variants: number;
  concurrency?: number;
  cleanup_losers?: boolean;
  exposeWinnerWorktreeApply?: boolean;
  ctx: TournamentContext;
  /**
   * Run a single variant (child agent loop) and return its finalText
   * plus the scorer-ready data. Implementation is responsible for
   * calling the agent / spawnChildTurn.
   */
  runChild: (prep: PreparedVariant) => Promise<{ finalText: string }>;
  /**
   * Score a variant's finalText. Return `{ score, warning? }`. Implementations
   * should absorb thrown errors and return `score: 0` with a warning.
   */
  scoreChild: (
    prep: PreparedVariant,
    finalText: string,
  ) => Promise<{ score: number; warning?: string }>;
  /**
   * Prepare an ephemeral subdir for each variant before any child runs.
   * Factored out so callers can share `prepareSpawnDir` without
   * Tournament taking a hard dep on SpawnAgent.
   */
  prepareSpawnDir: (
    parentWorkspaceRoot: string,
    taskId: string,
  ) => Promise<{ spawnDir: string; spawnWorkspace: Workspace }>;
}

/**
 * Rank variants by score DESC; ties broken by variantIndex ASC. Returns
 * a new array — does not mutate the input.
 */
export function rankVariants(
  variants: TournamentVariantResult[],
): TournamentVariantResult[] {
  return [...variants].sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    return a.variantIndex - b.variantIndex;
  });
}

/**
 * Pick the winner's `finalText`. The winner is the first entry of a
 * ranked list (per `rankVariants`). Returns `""` when the ranking is
 * empty.
 */
export function mergeWinner(ranked: TournamentVariantResult[]): string {
  return ranked[0]?.finalText ?? "";
}

/**
 * Run N child variants in parallel (bounded by `concurrency`), score
 * each with the configured scorer, and return the ranked list.
 *
 * Each variant gets its OWN ephemeral subdirectory (see
 * `prepareSpawnDir`). Isolation + MAX_SPAWN_DEPTH enforcement are
 * inherited from the single-spawn path via the caller's `runChild`
 * closure.
 */
export async function runTournament(
  options: RunTournamentOptions,
): Promise<TournamentResult> {
  const { variants, ctx, runChild, scoreChild, prepareSpawnDir } = options;
  const concurrency = Math.min(
    TOURNAMENT_MAX_CONCURRENCY,
    Math.max(1, options.concurrency ?? variants),
  );

  // Phase 1 — prepare all spawnDirs up-front so filesystem-check tests
  // can assert the `.tournament-n` layout exists regardless of scheduling.
  const builds: PreparedVariant[] = [];
  for (let n = 0; n < variants; n++) {
    const taskId = `${ctx.turnId}.tournament-${n}`;
    const prepared = await prepareSpawnDir(ctx.workspaceRoot, taskId);
    builds.push({
      variantIndex: n,
      taskId,
      spawnDir: prepared.spawnDir,
      spawnWorkspace: prepared.spawnWorkspace,
    });
  }

  // Phase 2 — run with a simple concurrency semaphore. We iterate in
  // insertion order; `concurrency=1` yields strict sequential execution,
  // which the test harness relies on for deterministic ordering.
  const results: TournamentVariantResult[] = new Array(variants);

  let cursor = 0;
  async function worker(): Promise<void> {
    for (;;) {
      const idx = cursor++;
      if (idx >= builds.length) return;
      const build = builds[idx]!;
      let finalText = "";
      try {
        const childResult = await runChild(build);
        finalText = childResult.finalText;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        finalText = `error: ${msg}`;
      }
      const { score, warning } = await scoreChild(build, finalText);
      if (warning) {
        ctx.stageAuditEvent("tournament_scorer_warning", {
          variantIndex: build.variantIndex,
          warning,
        });
      }
      results[build.variantIndex] = {
        variantIndex: build.variantIndex,
        score,
        finalText,
        spawnDir: build.spawnDir,
      };
    }
  }

  const workers: Promise<void>[] = [];
  for (let i = 0; i < concurrency; i++) workers.push(worker());
  await Promise.all(workers);

  // Rank by score DESC; ties broken by variantIndex ASC.
  const ranked = rankVariants(results);
  const winner = ranked[0]!;
  const winnerIndex = winner.variantIndex;
  const winnerWorktreeApply = options.exposeWinnerWorktreeApply === true
    ? {
        action: "preview" as const,
        spawnDir: winner.spawnDir,
      }
    : undefined;

  ctx.emitAgentEvent?.({
    type: "tournament_result",
    variants: results,
    winnerIndex,
    ...(winnerWorktreeApply ? { winnerWorktreeApply } : {}),
  });

  if (options.cleanup_losers === true) {
    await Promise.all(
      results
        .filter((r) => r.variantIndex !== winnerIndex)
        .map((r) =>
          fs.rm(r.spawnDir, { recursive: true, force: true }).catch(() => {
            /* best-effort cleanup */
          }),
        ),
    );
  }

  return {
    mode: "tournament",
    winnerIndex,
    variants: results,
    ...(winnerWorktreeApply ? { winnerWorktreeApply } : {}),
  };
}

/**
 * Helper re-exported for tests — compose the winner's finalText off a
 * full variant list (rank + merge in one step).
 */
export function selectWinnerText(variants: TournamentVariantResult[]): {
  winnerIndex: number;
  finalText: string;
} {
  const ranked = rankVariants(variants);
  const top = ranked[0];
  if (!top) return { winnerIndex: -1, finalText: "" };
  return { winnerIndex: top.variantIndex, finalText: mergeWinner(ranked) };
}

/** Silence unused-var lint in file-only use of path/Workspace types. */
void path;
