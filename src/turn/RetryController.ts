import { isResearchProofBlockReason } from "./ResearchProofFailureNotice.js";

export type RetryBlockKind =
  | "before_commit_blocked"
  | "structured_output_invalid"
  | "edit_apply_failed"
  | "max_attempts_exceeded";

export type RetryToolPolicy = "normal" | "text_only";

export interface RetryInput {
  kind: RetryBlockKind;
  reason: string;
  attempt: number;
  errorCode?: string;
}

export type RetryDecision =
  | {
      action: "resample";
      hiddenUserMessage: string;
      toolPolicy: RetryToolPolicy;
    }
  | {
      action: "abort";
      reason: string;
    };

function researchProofRewriteMessage(reason: string): string {
  return [
    "Your previous draft was blocked by the research proof verifier.",
    `Verifier reason:\n${reason}`,
    "Rewrite the final answer as plain text only.",
    "Use only the already inspected sources listed in the verifier reason.",
    "Cite every factual claim with the source id that supports it, for example [src_1].",
    "Do not call tools, browse, search, fetch, or introduce new sources.",
    "Remove any claim that is not directly supported by the inspected source ids.",
  ].join("\n\n");
}

function goalProgressToolFirstMessage(reason: string): string {
  return [
    "Your previous draft was blocked by the runtime goal-progress verifier.",
    `Verifier reason:\n${reason}`,
    "",
    "You must use the necessary tool or runtime action before writing another final answer.",
    "Do not answer with another promise, plan, dispatch announcement, or status update.",
    "Call the next required tool now. Examples include SpawnAgent, Browser, SocialBrowser, KnowledgeSearch, FileRead, FileWrite, FileDeliver, Bash, Calculation, or the exact tool required by the user request.",
    "After tool evidence is available, synthesize the actual result. If a hard blocker remains after concrete attempts, report that blocker with the evidence.",
  ].join("\n");
}

function interactiveToolFirstMessage(reason: string): string {
  return [
    "Your previous draft was blocked by the runtime interactive-work verifier.",
    `Verifier reason:\n${reason}`,
    "",
    "This request requires browser or GUI evidence.",
    "Use Browser or SocialBrowser for the next concrete action before writing another final answer.",
    "Do not answer with only text saying you will open, click, inspect, or test.",
    "If the Browser/SocialBrowser tools are not available in the exposed tool list, state that as the concrete blocker. Otherwise call the tool now.",
  ].join("\n");
}

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

    if (input.kind === "edit_apply_failed") {
      return {
        action: "resample",
        toolPolicy: "normal",
        hiddenUserMessage: this.editReflectionMessage(input),
      };
    }

    if (
      input.kind === "before_commit_blocked" &&
      isResearchProofBlockReason(input.reason)
    ) {
      return {
        action: "resample",
        toolPolicy: "text_only",
        hiddenUserMessage: researchProofRewriteMessage(input.reason),
      };
    }

    if (
      input.kind === "before_commit_blocked" &&
      input.reason.includes("GOAL_PROGRESS_EXECUTE_NEXT")
    ) {
      return {
        action: "resample",
        toolPolicy: "normal",
        hiddenUserMessage: goalProgressToolFirstMessage(input.reason),
      };
    }

    if (
      input.kind === "before_commit_blocked" &&
      input.reason.includes("INTERACTIVE_TOOL_REQUIRED")
    ) {
      return {
        action: "resample",
        toolPolicy: "normal",
        hiddenUserMessage: interactiveToolFirstMessage(input.reason),
      };
    }

    return {
      action: "resample",
      toolPolicy: "normal",
      hiddenUserMessage:
        "Your previous draft was blocked by a runtime verifier. " +
        `Reason: ${input.reason}. ` +
        "Produce a corrected answer that directly addresses the issue. " +
        "Do not repeat the unsupported or invalid claim.",
    };
  }

  private editReflectionMessage(input: RetryInput): string {
    if (input.errorCode === "not_unique") {
      return (
        "Your FileEdit failed: old_string appears more than once. " +
        `Reason: ${input.reason}. ` +
        "Re-read the file and extend old_string with more surrounding context lines to make it unique."
      );
    }
    if (input.errorCode === "lazy_output") {
      return (
        "Your FileEdit was blocked: new_string contains a placeholder comment (e.g. '// ... existing code'). " +
        `Reason: ${input.reason}. ` +
        "Write the complete replacement code — never use placeholder or abbreviated comments."
      );
    }
    return (
      "Your FileEdit failed: old_string was not found in the file. " +
      `Reason: ${input.reason}. ` +
      "Re-read the file with FileRead and retry with the exact old_string copied from the file content."
    );
  }
}
