/**
 * Custom classifier dimension registry.
 *
 * Lets external configs (magi.config.yaml) add custom classifier
 * dimensions to the existing Haiku meta-classifier calls without
 * modifying built-in classifier logic.
 */

export interface ClassifierDimensionDef {
  name: string;
  phase: "request" | "finalAnswer";
  schema: Record<string, string>;
  instructions: string;
}

const MAX_CUSTOM_DIMENSIONS = 10;

export class ClassifierExtensionRegistry {
  private readonly dimensions = new Map<string, ClassifierDimensionDef>();

  register(dim: ClassifierDimensionDef): void {
    if (this.dimensions.size >= MAX_CUSTOM_DIMENSIONS) {
      throw new Error(`max ${MAX_CUSTOM_DIMENSIONS} custom classifier dimensions allowed`);
    }
    this.dimensions.set(dim.name, dim);
  }

  unregister(name: string): boolean {
    return this.dimensions.delete(name);
  }

  getByPhase(phase: "request" | "finalAnswer"): ClassifierDimensionDef[] {
    return Array.from(this.dimensions.values()).filter((d) => d.phase === phase);
  }

  buildExtendedSystemPrompt(base: string, phase: "request" | "finalAnswer"): string {
    const dims = this.getByPhase(phase);
    if (dims.length === 0) return base;

    const extensions = dims
      .map((dim) => {
        const schemaLines = Object.entries(dim.schema)
          .map(([key, type]) => `    ${key}: ${type}`)
          .join("\n");
        return `\n// Custom dimension: ${dim.name}\n${dim.instructions}\nOutput schema for ${dim.name}:\n{\n${schemaLines}\n}`;
      })
      .join("\n");

    return `${base}\n\n## Custom Classifier Dimensions\n${extensions}`;
  }

  parseExtendedOutput(
    raw: Record<string, unknown>,
    phase: "request" | "finalAnswer",
  ): Record<string, unknown> | undefined {
    const dims = this.getByPhase(phase);
    if (dims.length === 0) return undefined;

    const result: Record<string, unknown> = {};
    for (const dim of dims) {
      if (dim.name in raw) {
        result[dim.name] = raw[dim.name];
      }
    }
    return Object.keys(result).length > 0 ? result : undefined;
  }

  estimateExtraTokens(): number {
    let total = 0;
    for (const dim of this.dimensions.values()) {
      total += Math.ceil(dim.instructions.length / 4) + Object.keys(dim.schema).length * 10;
    }
    return total;
  }

  get size(): number {
    return this.dimensions.size;
  }

  list(): ClassifierDimensionDef[] {
    return Array.from(this.dimensions.values());
  }
}
