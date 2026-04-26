import { describe, expect, it } from "vitest";
import type { LLMMessage } from "./LLMClient.js";
import { normalizeToolUseIdsForRequest } from "./LLMClient.js";

describe("normalizeToolUseIdsForRequest", () => {
  it("normalizes invalid tool_use ids and keeps matching tool_result references aligned", () => {
    const messages: LLMMessage[] = [
      {
        role: "assistant",
        content: [
          {
            type: "tool_use",
            id: "toolu_01Ca:bad.id+$",
            name: "ReadSpreadsheet",
            input: { path: "sales.xlsx" },
          },
        ],
      },
      {
        role: "user",
        content: [
          {
            type: "tool_result",
            tool_use_id: "toolu_01Ca:bad.id+$",
            content: "ok",
          },
        ],
      },
    ];

    const normalized = normalizeToolUseIdsForRequest(messages);
    const assistant = normalized[0];
    const user = normalized[1];

    expect(assistant).toMatchObject({
      role: "assistant",
      content: [
        {
          type: "tool_use",
          id: "toolu_01Ca_bad_id__",
          name: "ReadSpreadsheet",
          input: { path: "sales.xlsx" },
        },
      ],
    });
    expect(user).toMatchObject({
      role: "user",
      content: [
        {
          type: "tool_result",
          tool_use_id: "toolu_01Ca_bad_id__",
          content: "ok",
        },
      ],
    });
  });

  it("keeps already-valid ids unchanged", () => {
    const messages: LLMMessage[] = [
      {
        role: "assistant",
        content: [
          {
            type: "tool_use",
            id: "toolu_valid_123",
            name: "Bash",
            input: {},
          },
        ],
      },
    ];

    const normalized = normalizeToolUseIdsForRequest(messages);
    const assistant = normalized[0];

    expect(assistant).toMatchObject({
      role: "assistant",
      content: [
        {
          type: "tool_use",
          id: "toolu_valid_123",
          name: "Bash",
          input: {},
        },
      ],
    });
  });

  it("deduplicates sanitized collisions deterministically", () => {
    const messages: LLMMessage[] = [
      {
        role: "assistant",
        content: [
          {
            type: "tool_use",
            id: "call.one",
            name: "ToolA",
            input: {},
          },
          {
            type: "tool_use",
            id: "call:one",
            name: "ToolB",
            input: {},
          },
        ],
      },
      {
        role: "user",
        content: [
          { type: "tool_result", tool_use_id: "call.one", content: "a" },
          { type: "tool_result", tool_use_id: "call:one", content: "b" },
        ],
      },
    ];

    const normalized = normalizeToolUseIdsForRequest(messages);
    const assistant = normalized[0];
    const user = normalized[1];

    expect(assistant).toMatchObject({
      role: "assistant",
      content: [
        { type: "tool_use", id: "call_one", name: "ToolA", input: {} },
        { type: "tool_use", id: "call_one_1", name: "ToolB", input: {} },
      ],
    });
    expect(user).toMatchObject({
      role: "user",
      content: [
        { type: "tool_result", tool_use_id: "call_one", content: "a" },
        { type: "tool_result", tool_use_id: "call_one_1", content: "b" },
      ],
    });
  });
});
