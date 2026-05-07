import { describe, expect, it } from "vitest";
import { makeTaskBoardTool } from "./TaskBoard.js";

function assertArraySchemasDeclareItems(schema: unknown, path = "$"): void {
  if (!schema || typeof schema !== "object") return;
  const node = schema as Record<string, unknown>;

  if (node["type"] === "array") {
    expect(node, `${path} is an array schema without items`).toHaveProperty("items");
  }

  for (const [key, value] of Object.entries(node)) {
    assertArraySchemasDeclareItems(value, `${path}.${key}`);
  }
}

describe("TaskBoard input schema", () => {
  it("declares items for every array schema exposed to upstream providers", () => {
    const tool = makeTaskBoardTool("/tmp/magi-taskboard-schema-test");

    assertArraySchemasDeclareItems(tool.inputSchema);
  });
});
