import { describe, expect, it } from "vitest";
import {
  normalizeUserVisibleRouteMetaTags,
} from "./visibleText.js";

describe("visible text sanitizers", () => {
  it("localizes common Korean route META values to English when the reply is English", () => {
    const text = [
      "[META: intent=실행, domain=문서작성, complexity=복잡, route=서브에이전트]",
      "\nI will draft the memo from the source material.",
    ].join("");

    expect(normalizeUserVisibleRouteMetaTags(text)).toBe(
      "[META: intent=execution, domain=document writing, complexity=complex, route=subagent]" +
        "\nI will draft the memo from the source material.",
    );
  });

  it("localizes Korean coding experiment route META values to English when the reply is English", () => {
    const text = [
      "[META: intent=execution, domain=코딩/실험, complexity=complex, route=subagent]",
      "\nI'll spawn 4 helpers in parallel.",
    ].join("");

    expect(normalizeUserVisibleRouteMetaTags(text)).toBe(
      "[META: intent=execution, domain=coding/testing, complexity=complex, route=subagent]" +
        "\nI'll spawn 4 helpers in parallel.",
    );
  });

  it("localizes Korean AI orchestration route META values to English when the reply is English", () => {
    const text = [
      "[META: intent=execution, domain=AI오케스트레이션, complexity=complex, route=subagent]",
      "\nI'll coordinate the validators in parallel.",
    ].join("");

    expect(normalizeUserVisibleRouteMetaTags(text)).toBe(
      "[META: intent=execution, domain=AI orchestration, complexity=complex, route=subagent]" +
        "\nI'll coordinate the validators in parallel.",
    );
  });

  it("localizes common English route META values to Korean when the reply is Korean", () => {
    const text = [
      "[META: intent=execution, domain=document writing, complexity=complex, route=subagent]",
      "\n자료를 확인한 뒤 메모 초안을 작성하겠습니다.",
    ].join("");

    expect(normalizeUserVisibleRouteMetaTags(text)).toBe(
      "[META: intent=실행, domain=문서작성, complexity=복잡, route=서브에이전트]" +
        "\n자료를 확인한 뒤 메모 초안을 작성하겠습니다.",
    );
  });

  it("keeps the first route META tag and removes repeated route metadata tags", () => {
    const text = [
      "[META: intent=실행, domain=문서작성, complexity=complex, route=subagent]",
      "지금 바로 시작합니다.",
      "[META: intent=실행, domain=문서작성, complexity=simple, route=direct]",
      "[META: intent=실행, domain=문서작성, complexity=simple, route=direct]",
      "백그라운드 에이전트 기다리느라 시간 낭비했습니다.",
    ].join("");

    expect(normalizeUserVisibleRouteMetaTags(text)).toBe(
      "[META: intent=실행, domain=문서작성, complexity=복잡, route=서브에이전트]" +
        "지금 바로 시작합니다.백그라운드 에이전트 기다리느라 시간 낭비했습니다.",
    );
  });

  it("preserves non-routing META-like prose in the body", () => {
    const text = "설명: [META: this is part of the actual reply]";

    expect(normalizeUserVisibleRouteMetaTags(text)).toBe(text);
  });
});
