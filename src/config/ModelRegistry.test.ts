import { afterEach, describe, expect, it, vi } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ModelRegistry } from "./ModelRegistry.js";

function tempRegistryFile(contents: string): { dir: string; file: string } {
  const dir = mkdtempSync(join(tmpdir(), "model-registry-"));
  const file = join(dir, "model-registry.yaml");
  writeFileSync(file, contents, "utf8");
  return { dir, file };
}

function writeRegistry(file: string, modelId: string, maxOutput = 2048): void {
  writeFileSync(
    file,
    `models:
  ${modelId}:
    provider: anthropic
    aliases:
      - anthropic/${modelId}
    context_window: 200000
    max_output: ${maxOutput}
    thinking:
      type: adaptive
    temperature: 1
    capabilities:
      - tool_use
      - vision
    edit_format: search_replace
    pricing:
      input_per_mtok: 15
      output_per_mtok: 75
    provider_params:
      beta_header: test
`,
    "utf8",
  );
}

async function waitFor(predicate: () => boolean): Promise<void> {
  const started = Date.now();
  while (Date.now() - started < 5000) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error("timed out waiting for model registry reload");
}

describe("ModelRegistry", () => {
  const cleanup: string[] = [];

  afterEach(() => {
    for (const dir of cleanup.splice(0)) rmSync(dir, { recursive: true, force: true });
    vi.restoreAllMocks();
  });

  it("maps YAML fields into ModelCapability exactly", () => {
    const { dir, file } = tempRegistryFile("");
    cleanup.push(dir);
    writeRegistry(file, "claude-opus-4-7", 32000);

    const registry = new ModelRegistry({ path: file });

    expect(registry.getModel("claude-opus-4-7")).toEqual({
      id: "claude-opus-4-7",
      provider: "anthropic",
      contextWindow: 200000,
      maxOutput: 32000,
      thinking: { type: "adaptive" },
      temperature: 1,
      capabilities: ["tool_use", "vision"],
      editFormat: "search_replace",
      pricing: { inputPerMtok: 15, outputPerMtok: 75 },
      providerParams: { beta_header: "test" },
      aliases: ["anthropic/claude-opus-4-7"],
    });
  });

  it("resolves aliases and returns undefined for unknown models", () => {
    const { dir, file } = tempRegistryFile("");
    cleanup.push(dir);
    writeRegistry(file, "claude-sonnet-4-6", 16000);

    const registry = new ModelRegistry({ path: file });

    expect(registry.getModel("anthropic/claude-sonnet-4-6")?.id).toBe("claude-sonnet-4-6");
    expect(registry.getModel("missing-model")).toBeUndefined();
  });

  it("filters listModels by provider and capability", () => {
    const { dir, file } = tempRegistryFile(`models:
  claude-sonnet-4-6:
    provider: anthropic
    context_window: 200000
    max_output: 16000
    temperature: 1
    capabilities: [tool_use, vision]
    edit_format: search_replace
    pricing: { input_per_mtok: 3, output_per_mtok: 15 }
  gpt-5.5:
    provider: openai
    context_window: 1000000
    max_output: 128000
    temperature: 0.7
    capabilities: [tool_use]
    edit_format: whole
    pricing: { input_per_mtok: 5, output_per_mtok: 30 }
`);
    cleanup.push(dir);

    const registry = new ModelRegistry({ path: file });

    expect(registry.listModels({ provider: "anthropic" }).map((model) => model.id)).toEqual([
      "claude-sonnet-4-6",
    ]);
    expect(registry.listModels({ capability: "vision" }).map((model) => model.id)).toEqual([
      "claude-sonnet-4-6",
    ]);
  });

  it("hot reloads updated ConfigMap content", async () => {
    const { dir, file } = tempRegistryFile("");
    cleanup.push(dir);
    writeRegistry(file, "reload-model", 1024);

    const registry = new ModelRegistry({ path: file, watch: true, debounceMs: 10 });
    try {
      expect(registry.getModel("reload-model")?.maxOutput).toBe(1024);

      writeRegistry(file, "reload-model", 4096);

      await waitFor(() => registry.getModel("reload-model")?.maxOutput === 4096);
    } finally {
      registry.close();
    }
  });

  it("logs invalid YAML and keeps the previous registry", () => {
    const { dir, file } = tempRegistryFile("");
    cleanup.push(dir);
    writeRegistry(file, "stable-model", 2048);
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);

    const registry = new ModelRegistry({ path: file });
    writeFileSync(file, "models:\n  broken: [", "utf8");

    expect(registry.reload()).toBe(false);
    expect(registry.getModel("stable-model")?.maxOutput).toBe(2048);
    expect(errorSpy).toHaveBeenCalled();
  });

  it("loads the bundled model-registry.yaml", () => {
    const bundledPath = new URL("../../config/model-registry.yaml", import.meta.url);

    const registry = new ModelRegistry({ path: bundledPath.pathname });

    expect(registry.getModel("claude-opus-4-7")).toMatchObject({
      provider: "anthropic",
      contextWindow: 200000,
      maxOutput: 32000,
      editFormat: "search_replace",
    });
    expect(registry.getModel("anthropic/claude-opus-4-7")?.id).toBe("claude-opus-4-7");
    expect(registry.listModels({ capability: "vision" }).length).toBeGreaterThan(0);
  });
});
