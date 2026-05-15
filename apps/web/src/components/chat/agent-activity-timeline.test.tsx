import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { AgentActivityTimeline } from "./agent-activity-timeline";

describe("AgentActivityTimeline", () => {
  it("does not repeat the live thinking row as both header and detail", () => {
    vi.spyOn(Date, "now").mockReturnValue(16_000);

    try {
      const markup = renderToStaticMarkup(
        <AgentActivityTimeline live startedAt={1_000} />,
      );

      expect(markup.match(/15s 동안 작업/g)).toHaveLength(1);
    } finally {
      vi.restoreAllMocks();
    }
  });

  it("keeps live thinking content collapsed behind the activity row", () => {
    vi.spyOn(Date, "now").mockReturnValue(16_000);

    try {
      const markup = renderToStaticMarkup(
        createElement(AgentActivityTimeline, {
          live: true,
          startedAt: 1_000,
          thinkingContent: "Checking last week's sales\nComparing KPI targets",
        }),
      );

      expect(markup).toContain("15s 동안 작업");
      expect(markup).not.toContain("Checking last week&#x27;s sales");
      expect(markup).not.toContain("Comparing KPI targets");
    } finally {
      vi.restoreAllMocks();
    }
  });

  it("renders a structured phase label when runtime phase is available", () => {
    const markup = renderToStaticMarkup(
      createElement(AgentActivityTimeline, {
        live: true,
        startedAt: 1_000,
        turnPhase: "verifying",
      }),
    );

    expect(markup).toContain("Verifying results");
    expect(markup).not.toContain("15s 동안 작업");
  });

  it("summarizes completed thinking without rendering the raw details by default", () => {
    const markup = renderToStaticMarkup(
      createElement(AgentActivityTimeline, {
        thinkingDuration: 53,
        thinkingContent: "Internal notes should stay collapsed",
        collapsedByDefault: true,
      }),
    );

    expect(markup).toContain("53s 동안 작업");
    expect(markup).not.toContain("Internal notes should stay collapsed");
  });

  it("does not duplicate the duration label when a thought row is expanded", () => {
    const markup = renderToStaticMarkup(
      createElement(AgentActivityTimeline, {
        thinkingDuration: 88,
        thinkingContent: "Expanded notes stay visible",
        collapsedByDefault: false,
      }),
    );

    expect(markup.match(/88s 동안 작업/g)).toHaveLength(1);
    expect(markup).not.toContain("Expanded notes stay visible");
  });
});
