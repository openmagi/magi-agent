import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ControlRequestCard } from "./control-request";
import type { ControlRequestRecord } from "@/chat-core";

vi.mock("@/hooks/use-auth-fetch", () => ({
  useAuthFetch: () => vi.fn(),
}));

const digest = (char: string) => `sha256:${char.repeat(64)}`;

function controlRequest(
  proposedInput: unknown,
  overrides: Partial<ControlRequestRecord> = {},
): ControlRequestRecord {
  return {
    requestId: "cr_product_plane",
    kind: "tool_permission",
    state: "pending",
    sessionKey: "agent:main:app:general",
    channelName: "general",
    source: "turn",
    prompt: "Review controlled product-plane action",
    proposedInput,
    createdAt: 1,
    expiresAt: Date.now() + 60_000,
    ...overrides,
  };
}

describe("ControlRequestCard", () => {
  it("does not render ordinary user questions as control cards", () => {
    const request: ControlRequestRecord = {
      requestId: "turn_1:ask:1",
      kind: "user_question",
      state: "pending",
      sessionKey: "agent:main:app:general",
      channelName: "general",
      source: "turn",
      prompt: "Which file should I review?",
      proposedInput: {
        choices: [
          { id: "SOUL.md", label: "SOUL.md" },
          { id: "AGENTS.md", label: "AGENTS.md" },
        ],
      },
      createdAt: 1,
      expiresAt: Date.now() + 60_000,
    };

    const html = renderToStaticMarkup(
      <ControlRequestCard request={request} onRespond={() => {}} />,
    );

    expect(html).toBe("");
    expect(html).not.toContain("USER QUESTION");
    expect(html).not.toContain("user question");
    expect(html).not.toContain("Feedback");
  });

  it("renders a chat-native social browser connection card for Instagram asks", () => {
    const request: ControlRequestRecord = {
      requestId: "turn_1:ask:1",
      kind: "user_question",
      state: "pending",
      sessionKey: "agent:main:app:general",
      channelName: "general",
      source: "turn",
      prompt: "Connect Instagram to continue this request?",
      proposedInput: {
        choices: [
          { id: "social_browser_connect_instagram", label: "Open Instagram" },
          { id: "social_browser_cancel", label: "Cancel" },
        ],
        allowFreeText: false,
      },
      createdAt: 1,
      expiresAt: Date.now() + 60_000,
    };

    const html = renderToStaticMarkup(
      <ControlRequestCard request={request} onRespond={() => {}} />,
    );

    expect(html).toContain("Connect Instagram");
    expect(html).toContain("Open Instagram");
    expect(html).toContain("Continue after login");
    expect(html).toContain("Passwords stay in the browser session");
    expect(html).not.toContain("Feedback");
  });

  it("renders a PatchApply permission request as a safe patch summary", () => {
    const request: ControlRequestRecord = {
      requestId: "cr_patch",
      kind: "tool_permission",
      state: "pending",
      sessionKey: "agent:main:app:general",
      channelName: "general",
      source: "turn",
      prompt: "Review PatchApply changes before applying.",
      proposedInput: {
        toolName: "PatchApply",
        patch: "--- a/src/app.ts\n+++ b/src/app.ts\n@@ -1 +1 @@\n-secret\n+public\n",
        patchPreview: {
          dryRun: false,
          changedFiles: ["src/app.ts"],
          createdFiles: [],
          deletedFiles: [],
          files: [
            {
              path: "src/app.ts",
              operation: "update",
              hunks: 1,
              addedLines: 1,
              removedLines: 1,
              oldSha256: "old",
              newSha256: "new",
            },
          ],
        },
      },
      createdAt: 1,
      expiresAt: Date.now() + 60_000,
    };

    const html = renderToStaticMarkup(
      <ControlRequestCard request={request} onRespond={() => {}} />,
    );

    expect(html).toContain("Patch preview");
    expect(html).toContain("src/app.ts");
    expect(html).toContain("+1");
    expect(html).toContain("-1");
    expect(html).not.toContain("secret");
  });

  it("renders approval receipt digest context", () => {
    const request: ControlRequestRecord = {
      requestId: "approval-1",
      kind: "plan_approval",
      state: "pending",
      sessionKey: "agent:main:app:general",
      source: "plan",
      prompt: "Approve controlled action",
      createdAt: 1,
      expiresAt: Date.now() + 60_000,
      approvalReceiptPreview: {
        actionDigest: `sha256:${"1".repeat(64)}`,
        policySnapshotDigest: `sha256:${"2".repeat(64)}`,
        approvalScope: "selected_bot",
        approverGroup: "operators",
      },
    };

    const html = renderToStaticMarkup(
      <ControlRequestCard request={request} onRespond={() => {}} />,
    );

    expect(html).toContain("Approval scope");
    expect(html).toContain("selected_bot");
    expect(html).toContain("operators");
    expect(html).toContain("sha256:111111…111111");
    expect(html).toContain("sha256:222222…222222");
    expect(html).not.toContain("agent:main:app");
    expect(html).not.toMatch(/authorization|cookie|token/i);
  });

  it("renders auto permission self-review without claiming receiptless success", () => {
    const request = controlRequest({
      productPlaneControl: {
        kind: "auto_permission_self_review",
        executionState: "auto_executed",
        reviewId: "review:auto-permission",
        actionDigest: digest("a"),
        policySnapshotDigest: digest("b"),
        receiptState: "missing_receipt",
        reasonCodes: ["policy:auto_permission", "scope:bot"],
      },
    });

    const html = renderToStaticMarkup(
      <ControlRequestCard request={request} onRespond={() => {}} />,
    );

    expect(html).toContain("Auto Permission Self-Review");
    expect(html).toContain("Executed Automatically");
    expect(html).toContain("Receipt Missing");
    expect(html).toContain("receipt pending or missing");
    expect(html).toContain("review:auto-permission");
    expect(html).toContain("policy:auto_permission");
    expect(html).toContain("sha256:aaaaaa…aaaaaa");
    expect(html).toContain("sha256:bbbbbb…bbbbbb");
    expect(html).not.toContain("Delivered");
    expect(html).not.toContain("productPlaneControl");
    expect(html).not.toMatch(/<button[^>]*>\s*Approve\s*<\/button>/);
  });

  it("renders a hard guard block as blocked product-plane context", () => {
    const request = controlRequest({
      productPlaneControl: {
        kind: "hard_guard_block",
        executionState: "blocked",
        guardrailId: "guardrail:network-egress",
        policySnapshotDigest: digest("c"),
        receiptState: "pending",
        reasonCodes: ["guard:egress_blocked"],
      },
    });

    const html = renderToStaticMarkup(
      <ControlRequestCard request={request} onRespond={() => {}} />,
    );

    expect(html).toContain("Hard Guard Block");
    expect(html).toContain("Blocked");
    expect(html).toContain("guardrail:network-egress");
    expect(html).toContain("guard:egress_blocked");
    expect(html).toContain("Execution is blocked by product-plane guardrails.");
    expect(html).toContain("Deny");
    expect(html).not.toMatch(/<button[^>]*>\s*Approve\s*<\/button>/);
  });

  it("renders uncertain fail-passthrough as not executed pending review", () => {
    const request = controlRequest({
      productPlaneControl: {
        kind: "uncertain_fail_passthrough",
        executionState: "not_executed",
        approvalId: "approval:uncertain-classifier",
        policySnapshotDigest: digest("d"),
        receiptState: "pending",
        reasonCodes: ["verifier:uncertain", "policy:fail_passthrough"],
      },
    });

    const html = renderToStaticMarkup(
      <ControlRequestCard request={request} onRespond={() => {}} />,
    );

    expect(html).toContain("Uncertain Fail-Passthrough");
    expect(html).toContain("Not Executed");
    expect(html).toContain("Needs operator review");
    expect(html).toContain("approval:uncertain-classifier");
    expect(html).toContain("verifier:uncertain");
  });

  it("renders admin override required without adding activation controls", () => {
    const request = controlRequest({
      productPlaneControl: {
        kind: "admin_override_required",
        executionState: "pending_approval",
        approvalId: "approval:admin-override",
        overrideScope: "admin",
        actionDigest: digest("e"),
        policySnapshotDigest: digest("f"),
        receiptState: "pending",
        reasonCodes: ["override:admin_required"],
      },
    });

    const html = renderToStaticMarkup(
      <ControlRequestCard request={request} onRespond={() => {}} />,
    );

    expect(html).toContain("Admin Override Required");
    expect(html).toContain("Pending Approval");
    expect(html).toContain("Override scope");
    expect(html).toContain("admin");
    expect(html).toContain("approval:admin-override");
    expect(html).toContain("Approve");
    expect(html).not.toMatch(/activate|deploy|production route/i);
  });

  it("renders approval required as pending approval with receipt context", () => {
    const request = controlRequest({
      productPlaneControl: {
        kind: "approval_required",
        executionState: "pending_approval",
        approvalId: "approval:policy-gate",
        actionDigest: digest("1"),
        policySnapshotDigest: digest("2"),
        receiptId: "receipt:render-preview",
        receiptState: "rendered",
        reasonCodes: ["approval:required"],
      },
    });

    const html = renderToStaticMarkup(
      <ControlRequestCard request={request} onRespond={() => {}} />,
    );

    expect(html).toContain("Approval Required");
    expect(html).toContain("Pending Approval");
    expect(html).toContain("receipt:render-preview");
    expect(html).toContain("Rendered");
    expect(html).toContain("Approve");
    expect(html).toContain("Deny");
  });

  it("does not offer approve for denied hard invariants", () => {
    const request = controlRequest({
      productPlaneControl: {
        kind: "denied_hard_invariant",
        executionState: "denied",
        invariantId: "invariant:no-secret-egress",
        policySnapshotDigest: digest("3"),
        receiptState: "missing_receipt",
        reasonCodes: ["invariant:hard_denied"],
      },
    });

    const html = renderToStaticMarkup(
      <ControlRequestCard request={request} onRespond={() => {}} />,
    );

    expect(html).toContain("Denied Hard Invariant");
    expect(html).toContain("Denied");
    expect(html).toContain("invariant:no-secret-egress");
    expect(html).toContain("Hard invariants cannot be overridden from this UI.");
    expect(html).toContain("Deny");
    expect(html).not.toMatch(/<button[^>]*>\s*Approve\s*<\/button>/);
  });

  it("redacts unsafe product-plane control fields instead of rendering raw metadata", () => {
    const unsafe = {
      rawPrompt: ["raw", "prompt"].join("_"),
      rawOutput: ["raw", "model", "output"].join("_"),
      authHeader: ["Author", "ization"].join(""),
      cookieHeader: ["cook", "ie"].join(""),
      tokenValue: ["tok", "en"].join(""),
      sessionValue: ["sess", "ion"].join(""),
      privatePath: ["", "var", "lib", "openmagi", "runtime-state.sqlite"].join("/"),
      toolResult: ["tool", "result"].join("_"),
      transcript: ["turn", "transcript"].join("_"),
    };
    const request = controlRequest(
      {
        productPlaneControl: {
          kind: "approval_required",
          executionState: "pending_approval",
          approvalId: "approval:safe",
          guardrailId: unsafe.privatePath,
          reviewId: unsafe.rawPrompt,
          receiptState: "pending",
          reasonCodes: [
            `auth:${unsafe.authHeader}`,
            `state:${unsafe.sessionValue}`,
            "policy:safe_public_code",
          ],
          rawPrompt: unsafe.rawPrompt,
          rawOutput: unsafe.rawOutput,
          authHeader: unsafe.authHeader,
          cookieHeader: unsafe.cookieHeader,
          tokenValue: unsafe.tokenValue,
          sessionValue: unsafe.sessionValue,
          toolResult: unsafe.toolResult,
          transcript: unsafe.transcript,
        },
      },
      {
        approvalReceiptPreview: {
          actionDigest: digest("4"),
          policySnapshotDigest: digest("5"),
          approvalScope: unsafe.authHeader,
          approverGroup: unsafe.privatePath,
        },
      },
    );

    const html = renderToStaticMarkup(
      <ControlRequestCard request={request} onRespond={() => {}} />,
    );

    expect(html).toContain("Approval Required");
    expect(html).toContain("approval:safe");
    expect(html).toContain("policy:safe_public_code");
    expect(html).not.toContain("productPlaneControl");
    for (const value of Object.values(unsafe)) {
      expect(html).not.toContain(value);
    }
  });

  it("does not render raw product-plane metadata for unsupported control kinds", () => {
    const unsafe = {
      rawPrompt: ["raw", "prompt", "with", "session"].join("_"),
      rawOutput: ["raw", "model", "output"].join("_"),
      authHeader: ["Author", "ization"].join(""),
      cookieHeader: ["cook", "ie"].join(""),
      tokenValue: ["tok", "en"].join(""),
      privatePath: ["", "var", "lib", "openmagi", "runtime-state.sqlite"].join("/"),
    };
    const request = controlRequest({
      productPlaneControl: {
        kind: "future_runtime_mutation",
        executionState: "pending_approval",
        rawPayload: unsafe.rawPrompt,
        rawPrompt: unsafe.rawPrompt,
        rawOutput: unsafe.rawOutput,
        authHeader: unsafe.authHeader,
        cookieHeader: unsafe.cookieHeader,
        tokenValue: unsafe.tokenValue,
        privatePath: unsafe.privatePath,
        reasonCodes: [unsafe.rawPrompt, `auth:${unsafe.authHeader}`],
      },
    });

    const html = renderToStaticMarkup(
      <ControlRequestCard request={request} onRespond={() => {}} />,
    );

    expect(html).toContain("Unsupported Product-Plane Control");
    expect(html).toContain("No action is claimed from this projection.");
    expect(html).not.toContain("future_runtime_mutation");
    expect(html).not.toContain("productPlaneControl");
    for (const value of Object.values(unsafe)) {
      expect(html).not.toContain(value);
    }
    expect(html).not.toMatch(/<button[^>]*>\s*Approve\s*<\/button>/);
  });

  it("treats malformed explicit product-plane wrappers as unsupported metadata", () => {
    const malformed = ["raw", "prompt", "with", "session", "auth"].join("_");
    const request = controlRequest({
      productPlaneControl: malformed,
    });

    const html = renderToStaticMarkup(
      <ControlRequestCard request={request} onRespond={() => {}} />,
    );

    expect(html).toContain("Unsupported Product-Plane Control");
    expect(html).toContain("No action is claimed from this projection.");
    expect(html).not.toContain(malformed);
    expect(html).not.toContain("productPlaneControl");
    expect(html).not.toContain("Proposed tool input");
    expect(html).not.toMatch(/<button[^>]*>\s*Approve\s*<\/button>/);
  });

  it("keeps ordinary top-level kind payloads on the normal tool input path", () => {
    const request = controlRequest({
      kind: "approval_required",
      toolName: "SafeConfigPreview",
      target: "bot-policy-preview",
      valueDigest: digest("6"),
    });

    const html = renderToStaticMarkup(
      <ControlRequestCard request={request} onRespond={() => {}} />,
    );

    expect(html).toContain("Proposed tool input");
    expect(html).toContain("SafeConfigPreview");
    expect(html).toContain("bot-policy-preview");
    expect(html).toContain("Approve");
    expect(html).not.toContain("Product-plane control");
    expect(html).not.toContain("Approval Required");
    expect(html).not.toContain("Unsupported Product-Plane Control");
  });

  it("does not render delivered receipt state without a receipt or digest reference", () => {
    const request = controlRequest({
      productPlaneControl: {
        kind: "auto_permission_self_review",
        executionState: "auto_executed",
        reviewId: "review:receiptless",
        receiptState: "delivered",
        reasonCodes: ["policy:auto_permission"],
      },
    });

    const html = renderToStaticMarkup(
      <ControlRequestCard request={request} onRespond={() => {}} />,
    );

    expect(html).toContain("Auto Permission Self-Review");
    expect(html).toContain("receipt pending or missing");
    expect(html).toContain("Receipt Missing");
    expect(html).not.toContain("Delivered");
    expect(html).not.toContain("receipt-backed");
  });
});
