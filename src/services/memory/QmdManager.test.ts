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
const APP_BIN = "/app/node_modules/.bin/qmd";

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

    it("stays ready when only vector embedding fails", async () => {
      mockExecFile.mockImplementation(
        ((_bin: unknown, _args: unknown, _opts: unknown, cb: unknown) => {
          const args = _args as string[];
          if (args.includes("embed")) {
            (cb as (err: Error) => void)(
              new Error("sqlite-vec shared library unavailable"),
            );
            return;
          }
          (cb as (err: null, result: { stdout: string; stderr: string }) => void)(
            null,
            { stdout: "", stderr: "" },
          );
        }) as typeof execFile,
      );

      const mgr = new QmdManager(WORKSPACE, true);
      await mgr.start();

      expect(mgr.isReady()).toBe(true);
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
      expect(res[0].path).toBe("memory/daily/2026-04-22.md");
      expect(res[0].score).toBe(0.8);
      expect(res[1].context).toBe("root");
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
      expect(res[0].score).toBe(0.9);

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

  describe("hybridSearch()", () => {
    it("falls back to BM25-only when vectorEnabled=false", async () => {
      succeedWith();
      const mgr = new QmdManager(WORKSPACE, false);
      await mgr.start();

      const results = [{ path: "memory/daily/2026-04-22.md", content: "bm25 hit", score: 0.7 }];
      succeedWith(JSON.stringify({ results }));
      const res = await mgr.hybridSearch("test query");

      expect(res).toHaveLength(1);
      expect(res[0].content).toBe("bm25 hit");
      // Should only have called "search" (BM25), not "vsearch"
      const lastCalls = mockExecFile.mock.calls.slice(-1);
      const args = lastCalls[0][1] as string[];
      expect(args.includes("search")).toBe(true);
      expect(args.includes("vsearch")).toBe(false);
    });

    it("merges BM25 + vector results, dedupes by path (higher score wins)", async () => {
      succeedWith();
      const mgr = new QmdManager(WORKSPACE, true);
      await mgr.start();

      // Mock: alternate between BM25 and vector responses.
      // hybridSearch calls search() and vectorSearch() in parallel.
      // Each internally calls exec() which tries local bin first (may fail), then global.
      let execCount = 0;
      mockExecFile.mockImplementation(
        ((_bin: unknown, _args: unknown, _opts: unknown, cb: unknown) => {
          execCount++;
          const args = _args as string[];
          if (args.includes("search")) {
            (cb as (err: null, r: { stdout: string; stderr: string }) => void)(null, {
              stdout: JSON.stringify({
                results: [
                  { path: "memory/a.md", content: "bm25-a", score: 0.6 },
                  { path: "memory/b.md", content: "bm25-b", score: 0.5 },
                ],
              }),
              stderr: "",
            });
          } else if (args.includes("vsearch")) {
            (cb as (err: null, r: { stdout: string; stderr: string }) => void)(null, {
              stdout: JSON.stringify({
                results: [
                  { path: "memory/a.md", content: "vector-a", score: 0.9 },
                  { path: "memory/c.md", content: "vector-c", score: 0.8 },
                ],
              }),
              stderr: "",
            });
          } else {
            (cb as (err: null, r: { stdout: string; stderr: string }) => void)(null, {
              stdout: "",
              stderr: "",
            });
          }
        }) as typeof execFile,
      );

      const res = await mgr.hybridSearch("test", { limit: 5 });

      // a.md should have score 0.9 (vector wins over bm25 0.6)
      // c.md score 0.8 (vector only)
      // b.md score 0.5 (bm25 only)
      expect(res).toHaveLength(3);
      expect(res[0].path).toBe("memory/a.md");
      expect(res[0].score).toBe(0.9);
      expect(res[1].path).toBe("memory/c.md");
      expect(res[2].path).toBe("memory/b.md");
    });

    it("respects limit after merging", async () => {
      succeedWith();
      const mgr = new QmdManager(WORKSPACE, true);
      await mgr.start();

      mockExecFile.mockImplementation(
        ((_bin: unknown, _args: unknown, _opts: unknown, cb: unknown) => {
          const args = _args as string[];
          const results = args.includes("vsearch")
            ? [
                { path: "memory/v1.md", content: "v1", score: 0.95 },
                { path: "memory/v2.md", content: "v2", score: 0.85 },
                { path: "memory/v3.md", content: "v3", score: 0.75 },
              ]
            : [
                { path: "memory/b1.md", content: "b1", score: 0.8 },
                { path: "memory/b2.md", content: "b2", score: 0.7 },
              ];
          (cb as (err: null, r: { stdout: string; stderr: string }) => void)(null, {
            stdout: JSON.stringify({ results }),
            stderr: "",
          });
        }) as typeof execFile,
      );

      const res = await mgr.hybridSearch("test", { limit: 3 });

      expect(res).toHaveLength(3);
      // Top 3 by score: v1(0.95), v2(0.85), b1(0.8)
      expect(res[0].score).toBe(0.95);
      expect(res[1].score).toBe(0.85);
      expect(res[2].score).toBe(0.8);
    });

    it("returns [] when not ready", async () => {
      const mgr = new QmdManager(WORKSPACE, true);
      const res = await mgr.hybridSearch("test");
      expect(res).toEqual([]);
    });

    it("handles vector failure gracefully (BM25 results still returned)", async () => {
      succeedWith();
      const mgr = new QmdManager(WORKSPACE, true);
      await mgr.start();

      mockExecFile.mockImplementation(
        ((_bin: unknown, _args: unknown, _opts: unknown, cb: unknown) => {
          const args = _args as string[];
          if (args.includes("vsearch")) {
            (cb as (err: Error) => void)(new Error("embed model crashed"));
          } else if (args.includes("search")) {
            (cb as (err: null, r: { stdout: string; stderr: string }) => void)(null, {
              stdout: JSON.stringify({
                results: [{ path: "memory/bm25.md", content: "bm25 only", score: 0.6 }],
              }),
              stderr: "",
            });
          } else {
            (cb as (err: null, r: { stdout: string; stderr: string }) => void)(null, {
              stdout: "",
              stderr: "",
            });
          }
        }) as typeof execFile,
      );

      const res = await mgr.hybridSearch("test");
      // vectorSearch returns [] on error (fail-open), BM25 results survive
      expect(res).toHaveLength(1);
      expect(res[0].path).toBe("memory/bm25.md");
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
      const firstCall = mockExecFile.mock.calls[0];
      expect(firstCall[0]).toBe(LOCAL_BIN);
    });

    it("tries the image-local qmd binary before global qmd", async () => {
      mockExecFile.mockImplementation(
        ((_bin: unknown, _args: unknown, _opts: unknown, cb: unknown) => {
          if (_bin === LOCAL_BIN || _bin === APP_BIN) {
            (cb as (err: Error) => void)(new Error("ENOENT"));
            return;
          }
          (cb as (err: null, result: { stdout: string; stderr: string }) => void)(
            null,
            { stdout: "", stderr: "" },
          );
        }) as typeof execFile,
      );

      const mgr = new QmdManager(WORKSPACE, false);
      await mgr.start();

      const calledBins = mockExecFile.mock.calls.map((c) => c[0]);
      expect(calledBins).toContain(LOCAL_BIN);
      expect(calledBins).toContain(APP_BIN);
      expect(calledBins.indexOf(APP_BIN)).toBeLessThan(calledBins.indexOf("qmd"));
    });
  });
});
