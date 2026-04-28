import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { OutputArtifactRegistry } from "./OutputArtifactRegistry.js";

const roots: string[] = [];

async function makeRoot(): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "output-registry-"));
  roots.push(root);
  return root;
}

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => fs.rm(root, { recursive: true, force: true })),
  );
});

describe("OutputArtifactRegistry", () => {
  it("registers an artifact and persists delivery transitions", async () => {
    const root = await makeRoot();
    const registry = new OutputArtifactRegistry(root);

    const artifact = await registry.register({
      sessionKey: "s-1",
      turnId: "t-1",
      kind: "spreadsheet",
      format: "xlsx",
      title: "Q1 Revenue",
      filename: "q1-revenue.xlsx",
      mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      workspacePath: "exports/q1-revenue.xlsx",
      previewKind: "none",
      createdByTool: "SpreadsheetWrite",
      sourceKind: "structured",
    });

    await registry.markDeliveryPending(artifact.artifactId, {
      target: "chat",
      attemptCount: 1,
    });
    await registry.markDeliveryResult(artifact.artifactId, {
      target: "chat",
      status: "sent",
      attemptCount: 1,
      marker: "[attachment:abc:q1-revenue.xlsx]",
      externalId: "abc",
    });

    const persisted = await registry.get(artifact.artifactId);
    expect(persisted).toMatchObject({
      artifactId: artifact.artifactId,
      filename: "q1-revenue.xlsx",
      format: "xlsx",
      deliveries: [
        expect.objectContaining({
          target: "chat",
          status: "sent",
          attemptCount: 1,
          externalId: "abc",
        }),
      ],
    });

    const rawIndex = JSON.parse(
      await fs.readFile(path.join(root, "output-artifacts", "index.json"), "utf8"),
    ) as { artifacts: Array<{ artifactId: string }> };

    expect(rawIndex.artifacts).toHaveLength(1);
    expect(rawIndex.artifacts[0]?.artifactId).toBe(artifact.artifactId);
  });
});
