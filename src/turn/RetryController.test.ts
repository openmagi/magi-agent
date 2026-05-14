import { describe, expect, it } from "vitest";
import { RetryController } from "./RetryController.js";

describe("RetryController", () => {
  it("turns retryable before-commit blocks into resample actions", () => {
    const controller = new RetryController({ maxAttempts: 3 });
    expect(
      controller.next({
        kind: "before_commit_blocked",
        reason: "unsupported claim",
        attempt: 1,
      }),
    ).toMatchObject({
      action: "resample",
      hiddenUserMessage: expect.stringContaining("unsupported claim"),
    });
  });

  it("retries invalid structured output through the same transition", () => {
    const controller = new RetryController({ maxAttempts: 3 });
    expect(
      controller.next({
        kind: "structured_output_invalid",
        reason: "bad json",
        attempt: 1,
      }),
    ).toMatchObject({ action: "resample" });
  });

  it("aborts after max attempts", () => {
    const controller = new RetryController({ maxAttempts: 3 });
    expect(
      controller.next({
        kind: "max_attempts_exceeded",
        reason: "bad json",
        attempt: 3,
      }),
    ).toMatchObject({ action: "abort" });
  });

  it("handles edit_apply_failed with not_unique errorCode", () => {
    const controller = new RetryController({ maxAttempts: 3 });
    const result = controller.next({
      kind: "edit_apply_failed",
      reason: "old_string matches 3 locations",
      attempt: 1,
      errorCode: "not_unique",
    });
    expect(result).toMatchObject({ action: "resample", toolPolicy: "normal" });
    if (result.action === "resample") {
      expect(result.hiddenUserMessage).toContain("more than once");
      expect(result.hiddenUserMessage).toContain("more surrounding context");
    }
  });

  it("handles edit_apply_failed with lazy_output errorCode", () => {
    const controller = new RetryController({ maxAttempts: 3 });
    const result = controller.next({
      kind: "edit_apply_failed",
      reason: "placeholder detected",
      attempt: 1,
      errorCode: "lazy_output",
    });
    expect(result).toMatchObject({ action: "resample", toolPolicy: "normal" });
    if (result.action === "resample") {
      expect(result.hiddenUserMessage).toContain("placeholder");
    }
  });

  it("handles edit_apply_failed with default errorCode", () => {
    const controller = new RetryController({ maxAttempts: 3 });
    const result = controller.next({
      kind: "edit_apply_failed",
      reason: "not found",
      attempt: 1,
    });
    expect(result).toMatchObject({ action: "resample", toolPolicy: "normal" });
    if (result.action === "resample") {
      expect(result.hiddenUserMessage).toContain("not found in the file");
    }
  });

  it("uses text_only toolPolicy for research proof block", () => {
    const controller = new RetryController({ maxAttempts: 3 });
    const result = controller.next({
      kind: "before_commit_blocked",
      reason: "[RETRY:CLAIM_CITATION] missing citations",
      attempt: 1,
    });
    expect(result).toMatchObject({ action: "resample", toolPolicy: "text_only" });
    if (result.action === "resample") {
      expect(result.hiddenUserMessage).toContain("research proof verifier");
    }
  });

  it("uses normal toolPolicy for GOAL_PROGRESS_EXECUTE_NEXT block", () => {
    const controller = new RetryController({ maxAttempts: 3 });
    const result = controller.next({
      kind: "before_commit_blocked",
      reason: "GOAL_PROGRESS_EXECUTE_NEXT: must call a tool",
      attempt: 1,
    });
    expect(result).toMatchObject({ action: "resample", toolPolicy: "normal" });
    if (result.action === "resample") {
      expect(result.hiddenUserMessage).toContain("goal-progress");
    }
  });

  it("uses normal toolPolicy for INTERACTIVE_TOOL_REQUIRED block", () => {
    const controller = new RetryController({ maxAttempts: 3 });
    const result = controller.next({
      kind: "before_commit_blocked",
      reason: "INTERACTIVE_TOOL_REQUIRED: needs browser",
      attempt: 1,
    });
    expect(result).toMatchObject({ action: "resample", toolPolicy: "normal" });
    if (result.action === "resample") {
      expect(result.hiddenUserMessage).toContain("interactive-work");
    }
  });

  it("default resample has normal toolPolicy", () => {
    const controller = new RetryController({ maxAttempts: 3 });
    const result = controller.next({
      kind: "before_commit_blocked",
      reason: "generic block",
      attempt: 1,
    });
    expect(result).toMatchObject({ action: "resample", toolPolicy: "normal" });
  });
});
