import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ControlRequestCard } from "./control-request";
import type { ControlRequestRecord } from "@/lib/chat/types";

vi.mock("@/hooks/use-auth-fetch", () => ({
  useAuthFetch: () => vi.fn(),
}));

describe("ControlRequestCard", () => {
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
});
