/**
 * Tests for guardrails additions to toolResult.ts:
 * - toLlmFriendlyError: enriches ENOENT/EACCES/etc. with guidance
 * - summariseToolOutput: prefers llmOutput > transcriptOutput > output
 * - ERROR_GUIDANCE dictionary
 */

import { describe, it, expect } from "vitest";
import { errorResult, summariseToolOutput } from "./toolResult.js";

describe("toLlmFriendlyError", () => {
  it("enriches ENOENT with file-not-found guidance", () => {
    const err = Object.assign(new Error("no such file"), { code: "ENOENT" });
    const r = errorResult(err, Date.now());
    expect(r.errorMessage).toContain("no such file");
    expect(r.errorMessage).toContain("Glob");
  });

  it("enriches EACCES with permission guidance", () => {
    const err = Object.assign(new Error("permission denied"), { code: "EACCES" });
    const r = errorResult(err, Date.now());
    expect(r.errorMessage).toContain("permission denied");
    expect(r.errorMessage).toContain("read-only");
  });

  it("enriches EISDIR with directory guidance", () => {
    const err = Object.assign(new Error("is a directory"), { code: "EISDIR" });
    const r = errorResult(err, Date.now());
    expect(r.errorMessage).toContain("directory");
    expect(r.errorMessage).toContain("Glob");
  });

  it("truncates very long error messages", () => {
    const longMsg = "x".repeat(15_000);
    const err = new Error(longMsg);
    const r = errorResult(err, Date.now());
    expect(r.errorMessage!.length).toBeLessThan(longMsg.length);
    expect(r.errorMessage).toContain("truncated");
  });

  it("passes through normal errors without modification", () => {
    const err = new Error("some generic error");
    const r = errorResult(err, Date.now());
    expect(r.errorMessage).toBe("some generic error");
  });
});

describe("summariseToolOutput llmOutput preference", () => {
  it("prefers llmOutput over output when present", () => {
    const result = summariseToolOutput({
      status: "ok",
      output: "full verbose output",
      llmOutput: "compact LLM summary",
      durationMs: 1,
    } as ReturnType<typeof summariseToolOutput> extends string ? never : any);
    expect(result).toBe("compact LLM summary");
  });

  it("prefers transcriptOutput when llmOutput absent", () => {
    const result = summariseToolOutput({
      status: "ok",
      output: "full verbose output",
      transcriptOutput: "transcript summary",
      durationMs: 1,
    } as any);
    expect(result).toBe("transcript summary");
  });

  it("falls back to output when no alternatives", () => {
    const result = summariseToolOutput({
      status: "ok",
      output: "full verbose output",
      durationMs: 1,
    });
    expect(result).toBe("full verbose output");
  });
});
