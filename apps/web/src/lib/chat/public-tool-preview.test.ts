import { describe, expect, it } from "vitest";
import { derivePublicToolPreview } from "./public-tool-preview";

describe("derivePublicToolPreview", () => {
  it("summarizes truncated SpawnAgent prompt prefixes instead of showing only a generic assignment", () => {
    const preview = derivePublicToolPreview({
      label: "SpawnAgent",
      inputPreview:
        '{"persona":"skeptic-partner","prompt":"You are the SKEPTIC PARTNER.\\n\\nTask: Review Naeoe Distillery TIPS LP investment materials and identify market, financial, and legal risks.\\n\\nUse only the provided context...',
    });

    expect(preview).toEqual({
      action: "Assigning helper",
      target:
        "Task: Review Naeoe Distillery TIPS LP investment materials and identify market, financial, and legal risks.",
    });
    expect(JSON.stringify(preview)).not.toContain("skeptic-partner");
    expect(JSON.stringify(preview)).not.toContain("SKEPTIC PARTNER");
  });

  it("renders ModelProgress as model thinking progress instead of raw tool output", () => {
    expect(
      derivePublicToolPreview({
        label: "ModelProgress",
        inputPreview: JSON.stringify({
          stage: "waiting",
          label: "Drafting final answer",
          detail: "Synthesizing verified notes",
          elapsedMs: 12_000,
        }),
      }),
    ).toEqual({
      action: "Thinking through next step",
      target: "Drafting final answer",
      snippet: "Synthesizing verified notes",
    });
  });

  it("renders ModelProgress heartbeats as ongoing work", () => {
    expect(
      derivePublicToolPreview({
        label: "ModelProgress",
        language: "ko",
        inputPreview: JSON.stringify({
          stage: "heartbeat",
          elapsedMs: 45_000,
        }),
      }),
    ).toEqual({
      action: "계속 작업 중",
      target: "45초째 작업 중",
    });
  });

  it("localizes browser session output without exposing transport internals", () => {
    expect(
      derivePublicToolPreview({
        label: "Browser",
        language: "ko",
        outputPreview: JSON.stringify({
          action: "create_session",
          sessionId: "browser-session-fixture",
          cdpEndpoint: "ws://browser-worker.magi-system:9222/cdp/browser-session-fixture",
        }),
      }),
    ).toEqual({
      action: "브라우저 여는 중",
      target: "브라우저 세션 시작 중",
    });

    expect(
      derivePublicToolPreview({
        label: "Browser",
        language: "ko",
        outputPreview: JSON.stringify({
          action: "scrape",
          url: "https://example.com/report",
        }),
      }),
    ).toEqual({
      action: "페이지 읽는 중",
      target: "https://example.com/report",
    });
  });
});
