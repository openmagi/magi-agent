/**
 * StopReasonHandler unit tests (R3 refactor).
 *
 * Deterministic decision table over the 7 stop_reason cases plus the
 * recovery cap. Mutation contract on state + messages asserted.
 */

import { describe, it, expect } from "vitest";
import {
  handle,
  classifyStopReason,
  MAX_OUTPUT_TOKENS_RECOVERY_LIMIT,
  type StopReasonHandlerState,
  type StopReasonRaw,
} from "./StopReasonHandler.js";
import type { LLMContentBlock, LLMMessage } from "../transport/LLMClient.js";

function makeDeps() {
  const audits: Array<{ event: string; data?: Record<string, unknown> }> = [];
  const unknowns: Array<{ raw: StopReasonRaw; turnId: string }> = [];
  return {
    audits,
    unknowns,
    deps: {
      stageAuditEvent: (event: string, data?: Record<string, unknown>) => {
        audits.push({ event, ...(data !== undefined ? { data } : {}) });
      },
      logUnknown: (raw: string | null | undefined, turnId: string) => {
        unknowns.push({ raw: raw as StopReasonRaw, turnId });
      },
    },
  };
}

function makeState(recoveryAttempt = 0, len = 0): StopReasonHandlerState {
  return { recoveryAttempt, assistantTextSoFarLen: len };
}

describe("classifyStopReason", () => {
  it("maps each canonical wire value to itself", () => {
    for (const v of [
      "end_turn",
      "tool_use",
      "stop_sequence",
      "max_tokens",
      "refusal",
      "pause_turn",
    ] as const) {
      expect(classifyStopReason(v)).toBe(v);
    }
  });
  it("null / unexpected string → unknown", () => {
    expect(classifyStopReason(null)).toBe("unknown");
    expect(classifyStopReason(undefined)).toBe("unknown");
    expect(classifyStopReason("novel_reason")).toBe("unknown");
  });
});

describe("StopReasonHandler.handle — decision table", () => {
  it("end_turn → finalise, no audit", () => {
    const { audits, deps } = makeDeps();
    const state = makeState();
    const messages: LLMMessage[] = [];
    const d = handle(deps, state, {
      stopReasonRaw: "end_turn",
      blocks: [{ type: "text", text: "hi" }],
      iter: 0,
      turnId: "t",
      messages,
    });
    expect(d.kind).toBe("finalise");
    expect(audits.length).toBe(0);
    expect(messages.length).toBe(0);
    expect(state.recoveryAttempt).toBe(0);
  });

  it("stop_sequence → finalise, no audit", () => {
    const { audits, deps } = makeDeps();
    const d = handle(deps, makeState(), {
      stopReasonRaw: "stop_sequence",
      blocks: [],
      iter: 0,
      turnId: "t",
      messages: [],
    });
    expect(d.kind).toBe("finalise");
    expect(audits.length).toBe(0);
  });

  it("refusal → rule_check_violation audit, finalise", () => {
    const { audits, deps } = makeDeps();
    const d = handle(deps, makeState(), {
      stopReasonRaw: "refusal",
      blocks: [],
      iter: 2,
      turnId: "t",
      messages: [],
    });
    expect(d.kind).toBe("finalise");
    expect(audits.length).toBe(1);
    expect(audits[0]?.event).toBe("rule_check_violation");
    expect(audits[0]?.data?.reason).toBe("model_refusal");
    expect(audits[0]?.data?.iteration).toBe(2);
  });

  it("unknown → logUnknown + stop_reason_unknown audit, finalise", () => {
    const { audits, unknowns, deps } = makeDeps();
    const d = handle(deps, makeState(), {
      stopReasonRaw: "mystery" as unknown as StopReasonRaw,
      blocks: [],
      iter: 0,
      turnId: "T1",
      messages: [],
    });
    expect(d.kind).toBe("finalise");
    expect(unknowns.length).toBe(1);
    expect(unknowns[0]?.turnId).toBe("T1");
    expect(audits[0]?.event).toBe("stop_reason_unknown");
  });

  it("tool_use with blocks → run_tools", () => {
    const { deps } = makeDeps();
    const tu: Extract<LLMContentBlock, { type: "tool_use" }> = {
      type: "tool_use",
      id: "tu_1",
      name: "Echo",
      input: {},
    };
    const d = handle(deps, makeState(), {
      stopReasonRaw: "tool_use",
      blocks: [{ type: "text", text: "calling" }, tu],
      iter: 0,
      turnId: "t",
      messages: [],
    });
    expect(d.kind).toBe("run_tools");
    if (d.kind === "run_tools") {
      expect(d.toolUses.length).toBe(1);
      expect(d.toolUses[0]?.id).toBe("tu_1");
    }
  });

  it("tool_use with no tool_use blocks → finalise (defensive)", () => {
    const { deps } = makeDeps();
    const d = handle(deps, makeState(), {
      stopReasonRaw: "tool_use",
      blocks: [{ type: "text", text: "no tools" }],
      iter: 0,
      turnId: "t",
      messages: [],
    });
    expect(d.kind).toBe("finalise");
  });

  it("max_tokens within budget → recover, bumps counter, appends Continue.", () => {
    const { audits, deps } = makeDeps();
    const state = makeState(0, 10);
    const messages: LLMMessage[] = [];
    const d = handle(deps, state, {
      stopReasonRaw: "max_tokens",
      blocks: [{ type: "text", text: "partial" }],
      iter: 1,
      turnId: "t",
      messages,
    });
    expect(d.kind).toBe("recover");
    expect(state.recoveryAttempt).toBe(1);
    // Messages should contain assistant block then a Continue. user msg.
    expect(messages.length).toBe(2);
    expect(messages[0]?.role).toBe("assistant");
    expect(messages[1]?.role).toBe("user");
    expect(messages[1]?.content).toBe("Continue.");
    expect(audits.some((a) => a.event === "output_recovery")).toBe(true);
  });

  it("pause_turn shares recovery budget with max_tokens", () => {
    const { audits, deps } = makeDeps();
    const state = makeState(0);
    const d = handle(deps, state, {
      stopReasonRaw: "pause_turn",
      blocks: [{ type: "text", text: "paused" }],
      iter: 0,
      turnId: "t",
      messages: [],
    });
    expect(d.kind).toBe("recover");
    expect(state.recoveryAttempt).toBe(1);
    const rec = audits.find((a) => a.event === "output_recovery");
    expect(rec?.data?.stop_reason).toBe("pause_turn");
  });

  it("max_tokens at cap → exhausted audit, finalise", () => {
    const { audits, deps } = makeDeps();
    const state = makeState(MAX_OUTPUT_TOKENS_RECOVERY_LIMIT, 42);
    const d = handle(deps, state, {
      stopReasonRaw: "max_tokens",
      blocks: [{ type: "text", text: "!" }],
      iter: 5,
      turnId: "t",
      messages: [],
    });
    expect(d.kind).toBe("finalise");
    expect(state.recoveryAttempt).toBe(MAX_OUTPUT_TOKENS_RECOVERY_LIMIT);
    const ex = audits.find((a) => a.event === "output_recovery_exhausted");
    expect(ex).toBeDefined();
    expect(ex?.data?.finalLength).toBe(42);
    expect(ex?.data?.limit).toBe(MAX_OUTPUT_TOKENS_RECOVERY_LIMIT);
  });

  it("max_tokens with tool_use → tool_use blocks dropped + drop audit (codex gate2 P2)", () => {
    const { audits, deps } = makeDeps();
    const messages: LLMMessage[] = [];
    const d = handle(deps, makeState(), {
      stopReasonRaw: "max_tokens",
      blocks: [
        { type: "text", text: "calling " },
        { type: "tool_use", id: "tu_partial", name: "Bash", input: {} },
      ],
      iter: 0,
      turnId: "t",
      messages,
    });
    expect(d.kind).toBe("recover");
    // Messages[0].content must not contain any tool_use blocks.
    const asst = messages[0];
    expect(asst?.role).toBe("assistant");
    if (Array.isArray(asst?.content)) {
      expect(asst.content.some((b) => (b as { type: string }).type === "tool_use")).toBe(false);
    }
    const drop = audits.find(
      (a) => a.event === "output_recovery_drop_unresolved_tool_use",
    );
    expect(drop).toBeDefined();
    expect(drop?.data?.dropped).toBe(1);
  });

  it("max_tokens with only tool_use (empty filtered) → no assistant msg, still Continue.", () => {
    const { deps } = makeDeps();
    const messages: LLMMessage[] = [];
    const d = handle(deps, makeState(), {
      stopReasonRaw: "max_tokens",
      blocks: [{ type: "tool_use", id: "t", name: "Bash", input: {} }],
      iter: 0,
      turnId: "t",
      messages,
    });
    expect(d.kind).toBe("recover");
    // No assistant message was pushed because filtered blocks is empty.
    expect(messages.length).toBe(1);
    expect(messages[0]?.role).toBe("user");
    expect(messages[0]?.content).toBe("Continue.");
  });

});
