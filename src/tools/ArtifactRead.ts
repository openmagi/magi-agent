/**
 * ArtifactRead — T4-20 §7.12.a
 *
 * Reads an artifact at a chosen tier. Use L2 when you need structured
 * fields (cheap), L1 for a 2-line summary (also cheap), L0 for full
 * content (heaviest — only when you actually need to quote/transform).
 */

import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import type { ArtifactManager, ArtifactMeta } from "../artifacts/ArtifactManager.js";
import { errorResult } from "../util/toolResult.js";

export interface ArtifactReadInput {
  artifactId: string;
  tier?: "L0" | "L1" | "L2";
}

export interface ArtifactReadOutput {
  content: string;
  meta: ArtifactMeta;
  tier: "L0" | "L1" | "L2";
}

const ARTIFACT_READ_PREVIEW_LIMIT = 900;

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    artifactId: { type: "string" },
    tier: {
      type: "string",
      enum: ["L0", "L1", "L2"],
      description: "L0=full | L1=2-line overview | L2=structured abstract. Defaults to L0.",
    },
  },
  required: ["artifactId"],
} as const;

function previewArtifactReadForLlm(output: ArtifactReadOutput): string {
  const preview =
    output.content.length > ARTIFACT_READ_PREVIEW_LIMIT
      ? `${output.content.slice(0, ARTIFACT_READ_PREVIEW_LIMIT).trimEnd()}\n[truncated: ${output.content.length - ARTIFACT_READ_PREVIEW_LIMIT} chars omitted]`
      : output.content;
  return JSON.stringify({
    artifactId: output.meta.artifactId,
    title: output.meta.title,
    kind: output.meta.kind,
    tier: output.tier,
    sizeBytes: output.meta.sizeBytes,
    preview,
    fullReadAvailable: output.tier === "L0" && output.content.length > preview.length,
    path: output.meta.path,
  });
}

export function makeArtifactReadTool(
  manager: ArtifactManager,
): Tool<ArtifactReadInput, ArtifactReadOutput> {
  return {
    name: "ArtifactRead",
    description:
      "Read an artifact at a chosen tier. L2 (structured fields) and L1 (2-line summary) " +
      "are cheap and good for most situations. Only request L0 (full content) when you " +
      "need to quote or transform the original.",
    inputSchema: INPUT_SCHEMA,
    permission: "read",
    shouldDefer: true,
    async execute(
      input: ArtifactReadInput,
      _ctx: ToolContext,
    ): Promise<ToolResult<ArtifactReadOutput>> {
      const start = Date.now();
      try {
        const tier = input.tier ?? "L0";
        const meta = await manager.getMeta(input.artifactId);
        const content =
          tier === "L0"
            ? await manager.readL0(input.artifactId)
            : tier === "L1"
              ? await manager.readL1(input.artifactId)
              : await manager.readL2(input.artifactId);
        const output = { content, meta, tier };
        return {
          status: "ok",
          output,
          llmOutput: previewArtifactReadForLlm(output),
          durationMs: Date.now() - start,
        };
      } catch (err) {
        return errorResult(err, start);
      }
    },
  };
}
