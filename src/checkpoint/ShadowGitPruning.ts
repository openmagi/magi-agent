import path from "node:path";
import { runShadowGit } from "./ShadowGit.js";
import type { CheckpointEntry } from "./ShadowGit.js";

export interface PrunePolicy {
  hotCount: number;
  warmCount: number;
  maxAgeDays: number;
  maxSizeBytes: number;
  emergencyThresholdBytes: number;
}

export const DEFAULT_PRUNE_POLICY: PrunePolicy = {
  hotCount: 50,
  warmCount: 200,
  maxAgeDays: 7,
  maxSizeBytes: 1.5 * 1024 ** 3,
  emergencyThresholdBytes: 1.4 * 1024 ** 3,
};

export interface PruneResult {
  pruned: number;
  expired: number;
  emergency: boolean;
  beforeCount: number;
  afterCount: number;
}

export interface DetailedStorageUsage {
  totalCheckpoints: number;
  hotCount: number;
  warmCount: number;
  coldCount: number;
  sizeBytes: number;
  status: "ok" | "warn" | "emergency";
}

export function shouldPruneInline(
  checkpointCount: number,
  interval: number,
): boolean {
  return checkpointCount > 0 && checkpointCount % interval === 0;
}

async function getShadowGitSizeBytes(workspaceRoot: string): Promise<number> {
  const result = await runShadowGit(
    workspaceRoot,
    ["count-objects", "-v"],
    5_000,
  );
  if (result.code !== 0) return 0;

  let total = 0;
  for (const line of result.stdout.split("\n")) {
    const sizeMatch = line.match(/^size:\s+(\d+)/);
    const sizePackMatch = line.match(/^size-pack:\s+(\d+)/);
    if (sizeMatch) total += parseInt(sizeMatch[1]!, 10) * 1024;
    if (sizePackMatch) total += parseInt(sizePackMatch[1]!, 10) * 1024;
  }
  return total;
}

async function getAllCheckpoints(
  workspaceRoot: string,
): Promise<CheckpointEntry[]> {
  const log = await runShadowGit(
    workspaceRoot,
    ["log", "--format=%H%n%h%n%s%n%aI%n%b%n---ENTRY---"],
    10_000,
  );
  if (log.code !== 0) return [];

  const entries: CheckpointEntry[] = [];
  const blocks = log.stdout.split("---ENTRY---\n").filter((b) => b.trim());

  for (const block of blocks) {
    const lines = block.split("\n");
    if (lines.length < 4) continue;

    const fullSha = lines[0]!.trim();
    const sha = lines[1]!.trim();
    const subject = lines[2]!.trim();
    const timestamp = lines[3]!.trim();
    const body = lines.slice(4).join("\n");

    const turnMatch = body.match(/turn:\s*(\S+)/);
    const sessionMatch = body.match(/session:\s*(\S+)/);
    const filesMatch = body.match(/files:\s*(.+)/);
    const toolMatch = subject.match(/^checkpoint:\s*(\S+)/);

    entries.push({
      sha,
      fullSha,
      message: subject,
      timestamp,
      turnId: turnMatch?.[1] ?? "",
      sessionKey: sessionMatch?.[1] ?? "",
      toolName: toolMatch?.[1] ?? null,
      filesChanged:
        filesMatch?.[1]
          ?.split(",")
          .map((f) => f.trim())
          .filter(Boolean) ?? [],
    });
  }
  return entries;
}

/**
 * Rebuild the entire DAG keeping only selected commits.
 * Each kept commit preserves its original tree (file state).
 * Uses commit-tree to create new commits — no cherry-pick, no conflicts.
 */
async function rebuildDag(
  workspaceRoot: string,
  keptCommits: CheckpointEntry[],
): Promise<number> {
  if (keptCommits.length === 0) return 0;

  // keptCommits is newest-first — reverse to oldest-first for rebuilding
  const ordered = [...keptCommits].reverse();

  let parentSha: string | null = null;
  let newHeadSha = "";

  for (const cp of ordered) {
    // Get the tree hash of this commit
    const treeResult = await runShadowGit(
      workspaceRoot,
      ["log", "-1", "--format=%T", cp.fullSha],
      5_000,
    );
    if (treeResult.code !== 0) continue;
    const treeHash = treeResult.stdout.trim();

    const args = ["commit-tree", treeHash, "-m", cp.message];
    if (parentSha) {
      args.push("-p", parentSha);
    }

    const commitResult = await runShadowGit(workspaceRoot, args, 5_000);
    if (commitResult.code !== 0) continue;

    parentSha = commitResult.stdout.trim();
    newHeadSha = parentSha;
  }

  if (!newHeadSha) return 0;

  // Point HEAD at the rebuilt chain
  await runShadowGit(
    workspaceRoot,
    ["reset", "--soft", newHeadSha],
    5_000,
  );

  return keptCommits.length;
}

/**
 * Select which commits to keep from a range, applying squash rules:
 * - turnSquash: keep only the last commit per turnId
 * - sessionSquash: keep only the last commit per sessionKey
 */
function selectKeepers(
  commits: CheckpointEntry[],
  mode: "turn" | "session",
): CheckpointEntry[] {
  // commits are newest-first
  const seen = new Set<string>();
  const kept: CheckpointEntry[] = [];

  for (const cp of commits) {
    const key = mode === "turn"
      ? (cp.turnId || cp.fullSha)
      : (cp.sessionKey || cp.fullSha);
    if (!seen.has(key)) {
      seen.add(key);
      kept.push(cp);
    }
  }
  return kept;
}

export async function pruneCheckpoints(
  workspaceRoot: string,
  policy: PrunePolicy,
): Promise<PruneResult> {
  const result: PruneResult = {
    pruned: 0,
    expired: 0,
    emergency: false,
    beforeCount: 0,
    afterCount: 0,
  };

  const allCheckpoints = await getAllCheckpoints(workspaceRoot);
  result.beforeCount = allCheckpoints.length;

  if (allCheckpoints.length <= policy.hotCount) {
    result.afterCount = allCheckpoints.length;
    return result;
  }

  // Check emergency threshold
  const sizeBytes = await getShadowGitSizeBytes(workspaceRoot);
  if (sizeBytes > policy.emergencyThresholdBytes) {
    result.emergency = true;

    // Emergency: keep only hotCount commits
    const kept = allCheckpoints.slice(0, policy.hotCount);
    await rebuildDag(workspaceRoot, kept);

    // Aggressive GC
    await runShadowGit(
      workspaceRoot,
      ["reflog", "expire", "--expire=now", "--all"],
      10_000,
    );
    await runShadowGit(
      workspaceRoot,
      ["gc", "--prune=now", "--aggressive"],
      30_000,
    );

    const afterCheckpoints = await getAllCheckpoints(workspaceRoot);
    result.afterCount = afterCheckpoints.length;
    result.pruned = result.beforeCount - result.afterCount;
    return result;
  }

  // Normal pruning: build the set of commits to keep

  // Hot tier: keep as-is (newest hotCount commits)
  const hotCommits = allCheckpoints.slice(0, policy.hotCount);

  // Warm tier: squash to 1-per-turn
  const warmEnd = Math.min(allCheckpoints.length, policy.warmCount);
  const warmSlice = allCheckpoints.slice(policy.hotCount, warmEnd);
  const warmKept = selectKeepers(warmSlice, "turn");

  // Cold tier: squash to 1-per-session
  const coldSlice = allCheckpoints.slice(policy.warmCount);
  const coldKept = selectKeepers(coldSlice, "session");

  // Filter out expired (older than maxAgeDays)
  const cutoff = Date.now() - policy.maxAgeDays * 86400_000;
  const filterExpired = (cps: CheckpointEntry[]): CheckpointEntry[] =>
    cps.filter((cp) => {
      const cpTime = new Date(cp.timestamp).getTime();
      if (cpTime < cutoff) {
        result.expired++;
        return false;
      }
      return true;
    });

  const finalWarm = filterExpired(warmKept);
  const finalCold = filterExpired(coldKept);

  // Combine all kept commits (newest-first order preserved)
  const allKept = [...hotCommits, ...finalWarm, ...finalCold];

  if (allKept.length >= allCheckpoints.length) {
    result.afterCount = allCheckpoints.length;
    return result;
  }

  // Rebuild the DAG with only kept commits
  await rebuildDag(workspaceRoot, allKept);

  // GC unreachable objects
  await runShadowGit(
    workspaceRoot,
    ["reflog", "expire", "--expire=now", "--all"],
    10_000,
  );
  await runShadowGit(
    workspaceRoot,
    ["gc", "--prune=now"],
    30_000,
  );

  result.afterCount = (await getAllCheckpoints(workspaceRoot)).length;
  result.pruned = result.beforeCount - result.afterCount;
  return result;
}

export async function getDetailedStorageUsage(
  workspaceRoot: string,
  policy: PrunePolicy,
): Promise<DetailedStorageUsage> {
  const allCheckpoints = await getAllCheckpoints(workspaceRoot);
  const sizeBytes = await getShadowGitSizeBytes(workspaceRoot);

  const total = allCheckpoints.length;
  const hotCount = Math.min(total, policy.hotCount);
  const warmCount = Math.min(
    Math.max(0, total - policy.hotCount),
    policy.warmCount - policy.hotCount,
  );
  const coldCount = Math.max(0, total - policy.warmCount);

  let status: "ok" | "warn" | "emergency" = "ok";
  if (sizeBytes > policy.emergencyThresholdBytes) {
    status = "emergency";
  } else if (sizeBytes > policy.maxSizeBytes * 0.8) {
    status = "warn";
  } else if (total > policy.warmCount) {
    status = "warn";
  }

  return {
    totalCheckpoints: total,
    hotCount,
    warmCount,
    coldCount,
    sizeBytes,
    status,
  };
}
