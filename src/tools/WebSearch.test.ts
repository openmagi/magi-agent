import { describe, expect, it } from "vitest";
import { makeWebSearchTool } from "./WebSearch.js";

describe("WebSearch", () => {
  it("supports core-agent search tool aliases", () => {
    expect(makeWebSearchTool({ name: "WebSearch" }).name).toBe("WebSearch");
    expect(makeWebSearchTool({ name: "web-search" }).name).toBe("web-search");
    expect(makeWebSearchTool({ name: "web_search" }).name).toBe("web_search");
  });

  it("validates empty queries before network access", () => {
    const tool = makeWebSearchTool();

    expect(tool.validate?.({ query: "" })).toBe("Query must not be empty.");
  });
});
