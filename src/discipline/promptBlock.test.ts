/**
 * <discipline> prompt block formatting tests.
 */

import { describe, expect, it } from "vitest";
import { buildDisciplineBlock, formatRelative } from "./promptBlock.js";
import { DEFAULT_DISCIPLINE } from "./config.js";

describe("formatRelative", () => {
  it("just now under 1s", () => {
    expect(formatRelative(500)).toBe("just now");
  });
  it("seconds", () => {
    expect(formatRelative(3_000)).toBe("3s ago");
  });
  it("minutes", () => {
    expect(formatRelative(2 * 60_000)).toBe("2m ago");
  });
  it("hours", () => {
    expect(formatRelative(3 * 60 * 60_000)).toBe("3h ago");
  });
  it("days", () => {
    expect(formatRelative(2 * 24 * 60 * 60_000)).toBe("2d ago");
  });
});

describe("buildDisciplineBlock", () => {
  it("returns null when discipline is entirely off", () => {
    const block = buildDisciplineBlock({
      discipline: { ...DEFAULT_DISCIPLINE },
      counter: {
        sourceMutations: 0,
        testMutations: 0,
        dirtyFilesSinceCommit: 0,
      },
      now: 1000,
    });
    expect(block).toBeNull();
  });

  it("renders a coding-mode block with all expected lines", () => {
    const block = buildDisciplineBlock({
      discipline: {
        ...DEFAULT_DISCIPLINE,
        tdd: true,
        git: true,
        requireCommit: "soft",
        lastClassifiedMode: "coding",
      },
      counter: {
        sourceMutations: 7,
        testMutations: 2,
        dirtyFilesSinceCommit: 3,
        lastCommitAt: 0,
      },
      now: 60_000 + 1,
    });
    expect(block).not.toBeNull();
    expect(block).toContain("<discipline>");
    expect(block).toContain("</discipline>");
    expect(block).toContain("Mode: coding");
    expect(block).toContain("Source files modified this session: 7");
    expect(block).toContain("Test files modified this session: 2");
    expect(block).toContain("Ratio: 0.29");
    expect(block).toContain("Last git commit:");
    expect(block).toContain("Enforcement: soft");
    expect(block).toContain("Coding workspace:");
    expect(block).toContain("CodeWorkspace");
    expect(block).toContain("workspace/code/");
    expect(block).toContain('workspace_policy="git_worktree"');
    expect(block).toContain("RepoTaskState");
    expect(block).toContain("Commit units");
    expect(block).toContain("coding ledger");
    expect(block).toContain("workspace lock");
    expect(block).toContain("dirty workspace root");
    expect(block).toContain("No Docker-in-Docker");
    expect(block).toContain("in-workspace verification commands");
    expect(block).toContain("CodeIntelligence");
    expect(block).toContain("definition");
    expect(block).toContain("references");
    expect(block).toContain("CodeSymbolSearch");
    expect(block).toContain("CodeDiagnostics");
  });

  it("emits a suggestion when dirty files exceeds threshold", () => {
    const block = buildDisciplineBlock({
      discipline: {
        ...DEFAULT_DISCIPLINE,
        git: true,
        requireCommit: "soft",
        maxChangesBeforeCommit: 3,
      },
      counter: {
        sourceMutations: 0,
        testMutations: 0,
        dirtyFilesSinceCommit: 5,
      },
      now: 1000,
    });
    expect(block).toContain("Suggestion:");
    expect(block).toContain("CommitCheckpoint");
  });

  it("no-commit block shows 'none'", () => {
    const block = buildDisciplineBlock({
      discipline: {
        ...DEFAULT_DISCIPLINE,
        git: true,
        requireCommit: "soft",
      },
      counter: {
        sourceMutations: 0,
        testMutations: 0,
        dirtyFilesSinceCommit: 2,
      },
      now: 1000,
    });
    expect(block).toContain("Last git commit: none");
  });

  it("omits Ratio when tdd is off", () => {
    const block = buildDisciplineBlock({
      discipline: {
        ...DEFAULT_DISCIPLINE,
        git: true,
        tdd: false,
        requireCommit: "soft",
      },
      counter: {
        sourceMutations: 5,
        testMutations: 0,
        dirtyFilesSinceCommit: 1,
      },
      now: 1000,
    });
    expect(block).not.toContain("Ratio:");
  });
});
