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
});
