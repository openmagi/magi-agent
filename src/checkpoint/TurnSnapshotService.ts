/**
 * TurnSnapshotService — per-turn boundary snapshots using ShadowGit.
 *
 * Captures workspace state at turn start/end, stores diffs, and
 * provides surgical rollback via `git apply -R`.
 */

import type { ShadowGit, CheckpointMeta, CheckpointEntry } from "./ShadowGit.js";
import { runShadowGit } from "./ShadowGit.js";

const MAX_PATCH_BYTES = 512 * 1024;

export interface TurnSnapshot {
  turnId: string;
  sessionKey: string;
  startSha: string;
  endSha: string;
  patch: string | null;
  patchTruncated: boolean;
  filesChanged: string[];
  createdAt: number;
}

export class TurnSnapshotService {
  private readonly shadowGit: ShadowGit;
  private readonly startShas = new Map<string, string>();
  private readonly snapshots = new Map<string, TurnSnapshot>();

  constructor(shadowGit: ShadowGit) {
    this.shadowGit = shadowGit;
  }

  async snapshotTurnStart(
    turnId: string,
    sessionKey: string,
  ): Promise<string | null> {
    const meta: CheckpointMeta = {
      toolName: "turn_start",
      turnId,
      sessionKey,
      timestamp: Date.now(),
    };
    const sha = await this.shadowGit.createCheckpoint(meta);
    if (sha) {
      this.startShas.set(turnId, sha);
    }
    return sha;
  }

  async snapshotTurnEnd(
    turnId: string,
    sessionKey: string,
    startSha: string | null,
  ): Promise<TurnSnapshot | null> {
    const meta: CheckpointMeta = {
      toolName: "turn_end",
      turnId,
      sessionKey,
      timestamp: Date.now(),
    };
    const endSha = await this.shadowGit.createCheckpoint(meta);
    if (!endSha) return null;

    const resolvedStartSha = startSha ?? this.startShas.get(turnId);
    let patch: string | null = null;
    let patchTruncated = false;
    let filesChanged: string[] = [];

    if (resolvedStartSha) {
      const diffOutput = await this.shadowGit.diffCheckpoints(resolvedStartSha, endSha);
      if (diffOutput.length > MAX_PATCH_BYTES) {
        patchTruncated = true;
        patch = null;
      } else {
        patch = diffOutput;
      }
      filesChanged = this.extractFilesFromDiff(diffOutput);
    }

    const snap: TurnSnapshot = {
      turnId,
      sessionKey,
      startSha: resolvedStartSha ?? endSha,
      endSha,
      patch,
      patchTruncated,
      filesChanged,
      createdAt: Date.now(),
    };

    this.snapshots.set(turnId, snap);
    this.startShas.delete(turnId);
    return snap;
  }

  async rollbackTurn(turnId: string): Promise<{
    restoredSha: string;
    restoredFiles: string[];
  } | null> {
    const snap = this.snapshots.get(turnId);
    if (!snap) return null;

    const result = await this.shadowGit.restoreCheckpoint(snap.startSha);
    return {
      restoredSha: result.newSha,
      restoredFiles: result.restoredFiles,
    };
  }

  async rollbackToSha(sha: string): Promise<{
    restoredSha: string;
    restoredFiles: string[];
  }> {
    const result = await this.shadowGit.restoreCheckpoint(sha);
    return {
      restoredSha: result.newSha,
      restoredFiles: result.restoredFiles,
    };
  }

  async listTurnSnapshots(opts: {
    sessionKey?: string;
    limit?: number;
  }): Promise<TurnSnapshot[]> {
    const all = Array.from(this.snapshots.values());
    let filtered = all;
    if (opts.sessionKey) {
      filtered = all.filter((s) => s.sessionKey === opts.sessionKey);
    }
    const limit = opts.limit ?? 50;
    return filtered.slice(-limit);
  }

  getSnapshot(turnId: string): TurnSnapshot | undefined {
    return this.snapshots.get(turnId);
  }

  getStartSha(turnId: string): string | undefined {
    return this.startShas.get(turnId);
  }

  async pruneOlderThan(days: number): Promise<number> {
    const cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
    let pruned = 0;
    for (const [turnId, snap] of Array.from(this.snapshots.entries())) {
      if (snap.createdAt < cutoff) {
        this.snapshots.delete(turnId);
        pruned++;
      }
    }
    return pruned;
  }

  private extractFilesFromDiff(diff: string): string[] {
    const files: string[] = [];
    for (const line of diff.split("\n")) {
      if (line.startsWith("diff --git")) {
        const match = line.match(/b\/(.+)$/);
        if (match?.[1]) files.push(match[1]);
      }
    }
    return files;
  }
}
