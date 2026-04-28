export type RetryBlockKind =
  | "before_commit_blocked"
  | "structured_output_invalid"
  | "max_attempts_exceeded";

export interface RetryInput {
  kind: RetryBlockKind;
  reason: string;
  attempt: number;
}

export type RetryDecision =
  | {
      action: "resample";
      hiddenUserMessage: string;
    }
  | {
      action: "abort";
      reason: string;
    };

export class RetryController {
  constructor(private readonly opts: { maxAttempts: number }) {}

  next(input: RetryInput): RetryDecision {
    if (
      input.kind === "max_attempts_exceeded" ||
      input.attempt >= this.opts.maxAttempts
    ) {
      return {
        action: "abort",
        reason: input.reason,
      };
    }

    return {
      action: "resample",
      hiddenUserMessage:
        "Your previous draft was blocked by a runtime verifier. " +
        `Reason: ${input.reason}. ` +
        "Produce a corrected answer that directly addresses the issue. " +
        "Do not repeat the unsupported or invalid claim.",
    };
  }
}
