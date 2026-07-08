import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ChatMessages } from "./chat-messages";
import type { ChannelState, ChatMessage } from "@/chat-core";

// Standalone citation coverage (GAP #4). These integration cases render the real
// ChatMessages and assert the in-flight source-citation repair affordance
// (a labeled, localized indicator that supersedes the generic "Writing answer"
// state during a mid-turn attribution / grounding repair). They live here (not
// in the colocated chat-messages.test.tsx) so they are listed in the vitest
// include and run in the standard suite. chat-messages.test.tsx carries
// pre-existing dormant failures (typing-placeholder / live-run-chrome cases)
// that keep it out of the include.

function baseChannelState(overrides: Partial<ChannelState> = {}): ChannelState {
  return {
    streaming: false,
    streamingText: "",
    thinkingText: "",
    error: null,
    hasTextContent: false,
    thinkingStartedAt: null,
    activeTools: [],
    taskBoard: null,
    fileProcessing: false,
    turnPhase: null,
    heartbeatElapsedMs: null,
    pendingInjectionCount: 0,
    ...overrides,
  };
}

describe("ChatMessages source-citation repair affordance", () => {
  it("shows the citation-repair affordance during an attribution repair", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          turnPhase: "verifying",
          citationRepair: "attribution",
        })}
      />,
    );

    expect(html).toContain("citation-repair-indicator");
    expect(html).toContain("Revising answer with sources...");
  });

  it("shows the grounding affordance during an induce-search repair", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          turnPhase: "verifying",
          citationRepair: "induce_search",
        })}
      />,
    );

    expect(html).toContain("citation-repair-indicator");
    expect(html).toContain("Searching to ground claims...");
  });

  it("shows NO citation affordance on a normal streaming turn", () => {
    const html = renderToStaticMarkup(
      <ChatMessages
        ref={null}
        messages={[] satisfies ChatMessage[]}
        serverMessages={[] satisfies ChatMessage[]}
        channelState={baseChannelState({
          streaming: true,
          streamingText: "A normal answer.",
          hasTextContent: true,
          turnPhase: "executing",
          citationRepair: null,
        })}
      />,
    );

    expect(html).not.toContain("citation-repair-indicator");
    expect(html).toContain("A normal answer.");
  });
});
