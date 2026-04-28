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
});
