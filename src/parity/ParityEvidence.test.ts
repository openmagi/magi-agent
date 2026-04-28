import { describe, expect, it } from "vitest";
import type { ControlEvent } from "../control/ControlEvents.js";
import {
  buildParityEvidenceReport,
  evaluateControlEventParity,
  evaluateRuntimeParity,
} from "./ParityEvidence.js";

const READY_RUNTIME = {
  ok: true,
  degradedReasons: [],
  buildInfo: {
    version: "0.19.99",
    buildSha: "sha-test",
    imageRepo: "ghcr.io/test/core-agent",
    imageTag: "tag-test",
    imageDigest: "sha256:digest-test",
    builtImage: {
      buildSha: "sha-test",
      imageRepo: "ghcr.io/test/core-agent",
      imageTag: "tag-test",
      imageDigest: "sha256:digest-test",
    },
    expectedImage: {
      buildSha: "sha-test",
      imageRepo: "ghcr.io/test/core-agent",
      imageTag: "tag-test",
      imageDigest: "sha256:digest-test",
    },
  },
  features: {
    controlLedger: true,
    controlRequest: true,
    permissionArbiter: true,
    planApproval: true,
    structuredOutput: true,
    childHarness: true,
    transportReliability: true,
  },
  skills: {
    loaded: 3,
    issues: [],
    runtimeHooks: 1,
    executableRuntimeHooks: 1,
  },
};

function event<T extends ControlEvent>(
  seq: number,
  input: Omit<T, "v" | "eventId" | "seq" | "ts" | "sessionKey">,
): T {
  return {
    ...input,
    v: 1,
    eventId: `ce_${seq}`,
    seq,
    ts: 1_000 + seq,
    sessionKey: "agent:main:app:general:parity",
  } as T;
}

function readyControlEvents(): ControlEvent[] {
  const toolRequest = {
    requestId: "cr_tool",
    kind: "tool_permission" as const,
    state: "pending" as const,
    sessionKey: "agent:main:app:general:parity",
    source: "turn" as const,
    prompt: "Allow Bash?",
    createdAt: 1,
    expiresAt: 999_999,
  };
  const planRequest = {
    requestId: "cr_plan",
    kind: "plan_approval" as const,
    state: "pending" as const,
    sessionKey: "agent:main:app:general:parity",
    source: "plan" as const,
    prompt: "Approve plan?",
    createdAt: 2,
    expiresAt: 999_999,
  };

  return [
    event(1, { type: "control_request_created", request: toolRequest }),
    event(2, {
      type: "control_request_resolved",
      requestId: "cr_tool",
      decision: "approved",
      updatedInput: { command: "printf changed" },
    }),
    event(3, { type: "control_request_created", request: planRequest }),
    event(4, {
      type: "plan_lifecycle",
      planId: "plan-1",
      state: "approved",
      requestId: "cr_plan",
    }),
    event(5, {
      type: "control_request_resolved",
      requestId: "cr_plan",
      decision: "approved",
    }),
    event(6, {
      type: "plan_lifecycle",
      planId: "plan-1",
      state: "verification_pending",
    }),
    event(7, {
      type: "verification",
      turnId: "turn-1",
      status: "missing",
      reason: "completion claim needs evidence",
    }),
    event(8, {
      type: "retry",
      turnId: "turn-1",
      reason: "structured_output_invalid",
      attempt: 1,
      maxAttempts: 2,
      visibleToUser: true,
    }),
    event(9, {
      type: "structured_output",
      turnId: "turn-1",
      status: "retry_exhausted",
      schemaName: "canary",
    }),
    event(10, {
      type: "child_started",
      taskId: "child-1",
      parentTurnId: "turn-2",
    }),
    event(11, {
      type: "child_permission_decision",
      taskId: "child-1",
      decision: "deny",
      reason: "destructive rm -rf",
    }),
    event(12, {
      type: "child_completed",
      taskId: "child-1",
      summary: { ok: true },
    }),
    event(13, {
      type: "stop_reason",
      turnId: "turn-1",
      reason: "end_turn",
    }),
  ];
}

describe("ParityEvidence", () => {
  it("marks runtime parity ready when build identity, features, and skill hook telemetry are present", () => {
    const report = evaluateRuntimeParity(READY_RUNTIME);

    expect(report.ready).toBe(true);
    expect(report.missing).toEqual([]);
    expect(report.capabilities.every((capability) => capability.passed)).toBe(true);
  });

  it("fails runtime parity when deploy identity is incomplete", () => {
    const report = evaluateRuntimeParity({
      ...READY_RUNTIME,
      buildInfo: {
        ...READY_RUNTIME.buildInfo,
        imageDigest: null,
        builtImage: {
          ...READY_RUNTIME.buildInfo.builtImage,
          imageDigest: null,
        },
        expectedImage: {
          ...READY_RUNTIME.buildInfo.expectedImage,
          imageDigest: null,
        },
      },
    });

    expect(report.ready).toBe(false);
    expect(report.missing).toContain("deploy_identity");
  });

  it("fails runtime parity when baked and expected deploy identity do not match", () => {
    const report = evaluateRuntimeParity({
      ...READY_RUNTIME,
      buildInfo: {
        ...READY_RUNTIME.buildInfo,
        expectedImage: {
          ...READY_RUNTIME.buildInfo.expectedImage,
          imageDigest: "sha256:different-digest",
        },
      },
    });

    expect(report.ready).toBe(false);
    expect(report.missing).toContain("deploy_identity");
  });

  it("fails trusted skill hook parity when executable hooks are missing or skill loading has issues", () => {
    const noExecutableHooks = evaluateRuntimeParity({
      ...READY_RUNTIME,
      skills: {
        loaded: 3,
        issues: [],
        runtimeHooks: 1,
        executableRuntimeHooks: 0,
      },
    });
    const loadIssues = evaluateRuntimeParity({
      ...READY_RUNTIME,
      skills: {
        loaded: 3,
        issues: [{ message: "invalid runtime hook" }],
        runtimeHooks: 1,
        executableRuntimeHooks: 1,
      },
    });

    expect(noExecutableHooks.missing).toContain("trusted_skill_hooks");
    expect(loadIssues.missing).toContain("trusted_skill_hooks");
  });

  it("marks control-event parity ready only when the canary evidence covers the required lifecycle", () => {
    const report = evaluateControlEventParity(readyControlEvents());

    expect(report.ready).toBe(true);
    expect(report.missing).toEqual([]);
    expect(report.eventCount).toBe(13);
    expect(report.lastSeq).toBe(13);
    expect(report.checks.find((check) => check.id === "tool_permission_request")?.passed).toBe(true);
    expect(report.checks.find((check) => check.id === "child_lifecycle")?.passed).toBe(true);
  });

  it("reports precise missing control evidence instead of a vague parity failure", () => {
    const report = evaluateControlEventParity(
      readyControlEvents().filter((event) => event.type !== "structured_output"),
    );

    expect(report.ready).toBe(false);
    expect(report.missing).toContain("structured_output_retry");
  });

  it("requires plan and child lifecycle evidence to be tied to the same request or task", () => {
    const unrelatedLifecycleEvents = readyControlEvents().map((event) => {
      if (event.type === "plan_lifecycle") {
        return { ...event, requestId: "unrelated_plan_request" };
      }
      if (event.type === "child_permission_decision") {
        return { ...event, taskId: "unrelated-child-decision" };
      }
      if (event.type === "child_completed") {
        return { ...event, taskId: "unrelated-child-terminal" };
      }
      return event;
    });

    const report = evaluateControlEventParity(unrelatedLifecycleEvents);

    expect(report.ready).toBe(false);
    expect(report.missing).toContain("plan_approval_request");
    expect(report.missing).toContain("child_lifecycle");
  });

  it("combines runtime and control evidence into one deploy-train verdict", () => {
    const report = buildParityEvidenceReport({
      runtime: READY_RUNTIME,
      controlEvents: readyControlEvents(),
    });

    expect(report.ready).toBe(true);
    expect(report.runtime.ready).toBe(true);
    expect(report.control.ready).toBe(true);
  });
});
