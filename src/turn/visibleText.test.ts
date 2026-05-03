import { describe, expect, it } from "vitest";
import {
  normalizeUserVisibleRouteMetaTags,
} from "./visibleText.js";

describe("visible text sanitizers", () => {
  it("keeps the first route META tag and removes repeated route metadata tags", () => {
    const text = [
      "[META: intent=실행, domain=문서작성, complexity=complex, route=subagent]",
      "지금 바로 시작합니다.",
      "[META: intent=실행, domain=문서작성, complexity=simple, route=direct]",
      "[META: intent=실행, domain=문서작성, complexity=simple, route=direct]",
      "백그라운드 에이전트 기다리느라 시간 낭비했습니다.",
    ].join("");

    expect(normalizeUserVisibleRouteMetaTags(text)).toBe(
      "[META: intent=실행, domain=문서작성, complexity=complex, route=subagent]" +
        "지금 바로 시작합니다.백그라운드 에이전트 기다리느라 시간 낭비했습니다.",
    );
  });

  it("preserves non-routing META-like prose in the body", () => {
    const text = "설명: [META: this is part of the actual reply]";

    expect(normalizeUserVisibleRouteMetaTags(text)).toBe(text);
  });
});
