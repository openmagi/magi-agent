/**
 * Tests for src/util/toolResult.ts — errorResult / summariseToolOutput /
 * buildPreview. These functions were extracted from FileRead.ts and
 * Turn.ts; this suite pins behaviour so future moves remain safe.
 */

import { describe, it, expect } from "vitest";
import {
  errorResult,
  summariseToolOutput,
  buildPreview,
  buildToolInputPreview,
  summariseDelegatedPrompt,
} from "./toolResult.js";

describe("errorResult", () => {
  it("produces status:error with message from Error instance", () => {
    const before = Date.now();
    const r = errorResult(new Error("boom"), before - 5);
    expect(r.status).toBe("error");
    expect(r.errorMessage).toBe("boom");
    // `new Error("boom")` has no ErrnoException code; falls back to name.
    expect(r.errorCode).toBe("Error");
    expect(r.durationMs).toBeGreaterThanOrEqual(0);
  });

  it("handles non-Error thrown values via String() coercion", () => {
    const r = errorResult("string thrown", 0);
    expect(r.status).toBe("error");
    expect(r.errorMessage).toBe("string thrown");
    // Primitive: neither `.code` nor `.name`; falls through to "error".
    expect(r.errorCode).toBe("error");
  });

  it("preserves NodeJS.ErrnoException `.code` (e.g. ENOENT)", () => {
    const err = Object.assign(new Error("not found"), { code: "ENOENT" });
    const r = errorResult(err, Date.now());
    expect(r.errorCode).toBe("ENOENT");
    expect(r.errorMessage).toBe("not found");
  });
});

describe("summariseToolOutput", () => {
  it("returns string output verbatim on ok", () => {
    expect(
      summariseToolOutput({ status: "ok", output: "hello", durationMs: 1 }),
    ).toBe("hello");
  });

  it("JSON-encodes object output on ok", () => {
    expect(
      summariseToolOutput({ status: "ok", output: { a: 1 }, durationMs: 1 }),
    ).toBe('{"a":1}');
  });

  it("returns 'ok' when output is undefined", () => {
    expect(summariseToolOutput({ status: "ok", durationMs: 1 })).toBe("ok");
  });

  it("formats error status as 'error:<code> <message>'", () => {
    expect(
      summariseToolOutput({
        status: "error",
        errorCode: "X",
        errorMessage: "Y",
        durationMs: 1,
      }),
    ).toBe("error:X Y");
  });

  it("omits trailing space when errorMessage empty", () => {
    expect(
      summariseToolOutput({ status: "error", errorCode: "X", durationMs: 1 }),
    ).toBe("error:X");
  });
});

describe("buildPreview", () => {
  it("returns strings under cap unchanged", () => {
    expect(buildPreview("short")).toBe("short");
  });

  it("truncates long strings at 400 with '...' suffix", () => {
    const long = "a".repeat(500);
    const out = buildPreview(long);
    expect(out.length).toBe(403); // 400 chars + "..."
    expect(out.endsWith("...")).toBe(true);
    expect(out.startsWith("a".repeat(400))).toBe(true);
  });

  it("JSON.stringify(indent=2) for non-string inputs", () => {
    expect(buildPreview({ a: 1 })).toBe(JSON.stringify({ a: 1 }, null, 2));
  });

  it("returns '<unstringifiable>' on circular input", () => {
    const circ: Record<string, unknown> = {};
    circ.self = circ;
    expect(buildPreview(circ)).toBe("<unstringifiable>");
  });
});

describe("delegated prompt previews", () => {
  it("summarises delegated prompts without exposing the full private work order", () => {
    const prompt = [
      "You are the spawned child agent.",
      "Task: Review the investment materials",
      "Goal: produce a short risk memo",
      "Private context: this line should not be copied wholesale",
    ].join("\n");

    expect(summariseDelegatedPrompt(prompt)).toBe(
      "Task: Review the investment materials\nGoal: produce a short risk memo",
    );
  });

  it("uses the delegated prompt summary for SpawnAgent input previews", () => {
    const preview = buildToolInputPreview("SpawnAgent", {
      persona: "reviewer",
      prompt: [
        "You are the spawned child agent.",
        "Task: Review the investment materials",
        "Private context: do not leak this raw instruction",
      ].join("\n"),
    });

    expect(preview).toBe(JSON.stringify({
      prompt: "Task: Review the investment materials",
    }));
    expect(preview).not.toContain("Private context");
  });
});
