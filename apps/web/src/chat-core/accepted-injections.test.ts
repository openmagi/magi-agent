import { describe, expect, it } from "vitest";
import {
  consumeAcceptedInjections,
  markAcceptedInjectionsDrained,
  recordAcceptedInjection,
} from "./accepted-injections";

describe("accepted streaming injections", () => {
  it("marks accepted injections as drained only after the runtime confirms drain", () => {
    let state = recordAcceptedInjection({}, "general", {
      id: "injected-1",
      content: "Any news?",
      queuedAt: 1_800_000_000_000,
      drained: false,
    });

    state = markAcceptedInjectionsDrained(state, "general");

    expect(state.general?.[0]).toMatchObject({
      id: "injected-1",
      drained: true,
    });
  });

  it("returns unresolved accepted injections for next-turn fallback", () => {
    const state = recordAcceptedInjection({}, "general", {
      id: "injected-1",
      content: "Any news?",
      queuedAt: 1_800_000_000_000,
      drained: false,
    });

    const result = consumeAcceptedInjections(state, "general");

    expect(result.next.general).toBeUndefined();
    expect(result.consumed.map((item) => item.id)).toEqual(["injected-1"]);
    expect(result.unresolved.map((item) => item.content)).toEqual(["Any news?"]);
  });
});
