/**
 * QmdManager unit tests.
 *
 * Mocks node:child_process execFile to test all QmdManager paths
 * without requiring the qmd binary.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import path from "node:path";

// Mock child_process before importing QmdManager
vi.mock("node:child_process", () => {
  const mockExecFile = vi.fn();
  return { execFile: mockExecFile };
});

// Import after mock setup
import { execFile } from "node:child_process";
import { QmdManager } from "./QmdManager.js";

const WORKSPACE = "/tmp/test-workspace";
const MEMORY_DIR = path.join(WORKSPACE, "memory");
const LOCAL_BIN = path.join(WORKSPACE, "node_modules", ".bin", "qmd");

const mockExecFile = vi.mocked(execFile);

/**
 * Helper: make execFile call its callback with success.
 * promisify(execFile) calls execFile(bin, args, opts, callback).
 */
function succeedWith(stdout = "", stderr = ""): void {
  mockExecFile.mockImplementation(
    ((_bin: unknown, _args: unknown, _opts: unknown, cb: unknown) => {
      (cb as (err: null, result: { stdout: string; stderr: string }) => void)(
        null,
        { stdout, stderr },
      );
    }) as typeof execFile,
  );
}

/**
 * Helper: make execFile call its callback with an error.
 */
function failWith(message = "command not found"): void {
  mockExecFile.mockImplementation(
    ((_bin: unknown, _args: unknown, _opts: unknown, cb: unknown) => {
      (cb as (err: Error) => void)(new Error(message));
    }) as typeof execFile,
  );
}

/**
 * Helper: make local bin fail (first call) then global succeed (second call).
 * QmdManager.exec tries local first, then falls back to global.
 */
function localFailGlobalSucceed(stdout = "", stderr = ""): void {
  let callCount = 0;
  mockExecFile.mockImplementation(
    ((_bin: unknown, _args: unknown, _opts: unknown, cb: unknown) => {
      callCount++;
      if (callCount % 2 === 1) {
        // odd calls = local bin attempt → fail
        (cb as (err: Error) => void)(new Error("ENOENT"));
      } else {
        // even calls = global fallback → succeed
        (cb as (err: null, result: { stdout: string; stderr: string }) => void)(
          null,
          { stdout, stderr },
        );
      }
    }) as typeof execFile,
  );
}

describe("QmdManager", () => {
  beforeEach(() => {
    mockExecFile.mockReset();
  });

  describe("start()", () => {
    it("registers collection, runs update, and sets ready=true", async () => {
      succeedWith();
      const mgr = new QmdManager(WORKSPACE, false);
      expect(mgr.isReady()).toBe(false);

      await mgr.start();

      expect(mgr.isReady()).toBe(true);
      // Should have called execFile for: collection add (local+global fallback or direct),
      // then update. We check that "collection" and "update" args appeared.
      const allCalls = mockExecFile.mock.calls;
      const argSets = allCalls.map((c) => c[1] as string[]);
      expect(argSets.some((a) => a.includes("collection"))).toBe(true);
      expect(argSets.some((a) => a.includes("update"))).toBe(true);
    });

    it("also runs embed when vectorEnabled=true", async () => {
      succeedWith();
      const mgr = new QmdManager(WORKSPACE, true);

      await mgr.start();

      expect(mgr.isReady()).toBe(true);
      const argSets = mockExecFile.mock.calls.map((c) => c[1] as string[]);
      expect(argSets.some((a) => a.includes("embed"))).toBe(true);
    });

    it("does not run embed when vectorEnabled=false", async () => {
      succeedWith();
      const mgr = new QmdManager(WORKSPACE, false);

      await mgr.start();

      const argSets = mockExecFile.mock.calls.map((c) => c[1] as string[]);
      expect(argSets.some((a) => a.includes("embed"))).toBe(false);
    });

    it("sets ready=false on error (fail-open)", async () => {
      // collection add succeeds (or silently fails), but update throws
      let callIndex = 0;
      mockExecFile.mockImplementation(
        ((_bin: unknown, _args: unknown, _opts: unknown, cb: unknown) => {
          callIndex++;
          const args = _args as string[];
          // Let collection add succeed, but fail on update
          if (args.includes("update")) {
            (cb as (err: Error) => void)(new Error("qmd not found"));
          } else {
            (cb as (err: null, result: { stdout: string; stderr: string }) => void)(
              null,
              { stdout: "", stderr: "" },
            );
          }
        }) as typeof execFile,
      );

      const mgr = new QmdManager(WORKSPACE, false);
      await mgr.start();

      expect(mgr.isReady()).toBe(false);
    });
  });

  describe("search()", () => {
    it("returns parsed results on success", async () => {
      const results = [
        { path: "memory/daily/2026-04-22.md", content: "hello", score: 0.8 },
        { path: "memory/ROOT.md", content: "world", score: 0.5, context: "root" },
      ];
      succeedWith(JSON.stringify({ results }));

      const mgr = new QmdManager(WORKSPACE, false);
      // Manually set ready
      succeedWith();
      await mgr.start();

      // Now mock search response
      succeedWith(JSON.stringify({ results }));
      const res = await mgr.search("hello world");

      expect(res).toHaveLength(2);
      expect(res[0]!.path).toBe("memory/daily/2026-04-22.md");
      expect(res[0]!.score).toBe(0.8);
      expect(res[1]!.context).toBe("root");
    });

    it("returns [] when not ready", async () => {
      const mgr = new QmdManager(WORKSPACE, false);
      // Don't start — not ready

      const res = await mgr.search("hello");
      expect(res).toEqual([]);
      // Should not have called execFile
      expect(mockExecFile).not.toHaveBeenCalled();
    });

    it("returns [] on exec error (fail-open)", async () => {
      succeedWith();
      const mgr = new QmdManager(WORKSPACE, false);
      await mgr.start();

      // Now make exec fail
      failWith("qmd crashed");
      const res = await mgr.search("hello");
      expect(res).toEqual([]);
    });

    it("returns [] on invalid JSON (fail-open)", async () => {
      succeedWith();
      const mgr = new QmdManager(WORKSPACE, false);
      await mgr.start();

      succeedWith("not json {{{");
      const res = await mgr.search("hello");
      expect(res).toEqual([]);
    });

    it("passes custom opts to qmd CLI args", async () => {
      succeedWith();
      const mgr = new QmdManager(WORKSPACE, false);
      await mgr.start();

      succeedWith(JSON.stringify({ results: [] }));
      await mgr.search("test query", {
        collection: "knowledge",
        limit: 10,
        minScore: 0.7,
      });

      // Find the search call
      const searchCall = mockExecFile.mock.calls.find((c) => {
        const args = c[1] as string[];
        return args.includes("search");
      });
      expect(searchCall).toBeDefined();
      const args = searchCall![1] as string[];
      expect(args).toContain("--collection");
      expect(args).toContain("knowledge");
      expect(args).toContain("--limit");
      expect(args).toContain("10");
      expect(args).toContain("--min-score");
      expect(args).toContain("0.7");
    });
  });

  describe("vectorSearch()", () => {
    it("returns [] when vectorEnabled=false", async () => {
      succeedWith();
      const mgr = new QmdManager(WORKSPACE, false);
      await mgr.start();

      const res = await mgr.vectorSearch("hello");
      expect(res).toEqual([]);
    });

    it("returns [] when not ready", async () => {
      const mgr = new QmdManager(WORKSPACE, true);
      // Don't start

      const res = await mgr.vectorSearch("hello");
      expect(res).toEqual([]);
    });

    it("returns parsed results when vector enabled and ready", async () => {
      succeedWith();
      const mgr = new QmdManager(WORKSPACE, true);
      await mgr.start();

      const results = [
        { path: "memory/weekly/w17.md", content: "vector hit", score: 0.9 },
      ];
      succeedWith(JSON.stringify({ results }));
      const res = await mgr.vectorSearch("semantic query");

      expect(res).toHaveLength(1);
      expect(res[0]!.score).toBe(0.9);

      // Verify vsearch command was used
      const vsearchCall = mockExecFile.mock.calls.find((c) => {
        const args = c[1] as string[];
        return args.includes("vsearch");
      });
      expect(vsearchCall).toBeDefined();
    });

    it("returns [] on exec error (fail-open)", async () => {
      succeedWith();
      const mgr = new QmdManager(WORKSPACE, true);
      await mgr.start();

      failWith("embed model unavailable");
      const res = await mgr.vectorSearch("hello");
      expect(res).toEqual([]);
    });
  });

  describe("reindex()", () => {
    it("calls update + embed when vectorEnabled=true", async () => {
      succeedWith();
      const mgr = new QmdManager(WORKSPACE, true);
      await mgr.start();
      mockExecFile.mockClear();

      succeedWith();
      await mgr.reindex();

      const argSets = mockExecFile.mock.calls.map((c) => c[1] as string[]);
      expect(argSets.some((a) => a.includes("update"))).toBe(true);
      expect(argSets.some((a) => a.includes("embed"))).toBe(true);
    });

    it("calls update only when vectorEnabled=false", async () => {
      succeedWith();
      const mgr = new QmdManager(WORKSPACE, false);
      await mgr.start();
      mockExecFile.mockClear();

      succeedWith();
      await mgr.reindex();

      const argSets = mockExecFile.mock.calls.map((c) => c[1] as string[]);
      expect(argSets.some((a) => a.includes("update"))).toBe(true);
      expect(argSets.some((a) => a.includes("embed"))).toBe(false);
    });

    it("does nothing when not ready", async () => {
      const mgr = new QmdManager(WORKSPACE, false);
      // Don't start

      await mgr.reindex();
      expect(mockExecFile).not.toHaveBeenCalled();
    });

    it("silently swallows errors (non-fatal)", async () => {
      succeedWith();
      const mgr = new QmdManager(WORKSPACE, false);
      await mgr.start();
      mockExecFile.mockClear();

      failWith("disk full");
      // Should not throw
      await expect(mgr.reindex()).resolves.toBeUndefined();
    });
  });

  describe("stop()", () => {
    it("sets ready=false", async () => {
      succeedWith();
      const mgr = new QmdManager(WORKSPACE, false);
      await mgr.start();
      expect(mgr.isReady()).toBe(true);

      await mgr.stop();
      expect(mgr.isReady()).toBe(false);
    });
  });

  describe("exec fallback", () => {
    it("falls back to global qmd when local bin fails", async () => {
      localFailGlobalSucceed();
      const mgr = new QmdManager(WORKSPACE, false);
      await mgr.start();

      expect(mgr.isReady()).toBe(true);
      // Verify local bin was tried first
      const firstCall = mockExecFile.mock.calls[0]!;
      expect(firstCall[0]).toBe(LOCAL_BIN);
    });
  });
});
