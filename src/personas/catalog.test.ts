/**
 * Persona catalog tests — T2-11.
 *
 * Covers:
 *  1. builtin lookup (BUILTIN_PERSONAS has baseline + research roles).
 *  2. yaml override merges over builtin.
 *  3. wildcard "*" allowed_tools round-trips through load + resolve.
 *  4. malformed entries fall back to builtin silently.
 *  5. resolvePersona returns null for unknown names (free-form fallback).
 *  6. ENOENT for personas.yaml returns builtin unchanged.
 */

import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  ALLOWED_TOOLS_WILDCARD,
  BUILTIN_PERSONAS,
  loadPersonaCatalog,
  resolvePersona,
} from "./catalog.js";

describe("personas/catalog", () => {
  let tmpRoot: string;

  beforeEach(async () => {
    tmpRoot = await fs.mkdtemp(path.join(os.tmpdir(), "persona-catalog-"));
  });
  afterEach(async () => {
    await fs.rm(tmpRoot, { recursive: true, force: true });
  });

  it("(1) BUILTIN_PERSONAS exposes baseline and research roles", () => {
    expect(Object.keys(BUILTIN_PERSONAS).sort()).toEqual([
      "coder",
      "explore",
      "planner",
      "research",
      "reviewer",
      "scout",
      "synthesis",
    ]);
    expect(BUILTIN_PERSONAS.explore?.allowed_tools).toEqual([
      "FileRead",
      "Glob",
      "Grep",
    ]);
    expect(BUILTIN_PERSONAS.research?.allowed_tools).toEqual([
      "WebSearch",
      "WebFetch",
      "Browser",
      "KnowledgeSearch",
      "FileRead",
      "PackageDependencyResolve",
      "ExternalSourceCache",
      "ExternalSourceRead",
      "Glob",
      "Grep",
      "Clock",
      "DateRange",
      "Calculation",
      "ArtifactRead",
    ]);
    expect(BUILTIN_PERSONAS.synthesis?.allowed_tools).toEqual([]);
    expect(BUILTIN_PERSONAS.synthesis?.completion_contract).toMatchObject({
      required_evidence: "text",
      require_non_empty_result: true,
    });
    expect(BUILTIN_PERSONAS.research?.allowed_tools).toContain("PackageDependencyResolve");
    expect(BUILTIN_PERSONAS.scout?.allowed_tools).toContain("PackageDependencyResolve");
    expect(BUILTIN_PERSONAS.research?.allowed_tools).toContain("ExternalSourceCache");
    expect(BUILTIN_PERSONAS.scout?.allowed_tools).toContain("ExternalSourceCache");
    expect(BUILTIN_PERSONAS.scout?.allowed_tools).toContain("ExternalSourceRead");
    expect(BUILTIN_PERSONAS.coder?.allowed_tools).toBe(ALLOWED_TOOLS_WILDCARD);
    expect(BUILTIN_PERSONAS.coder?.system_prompt).toContain("CodeIntelligence");
  });

  it("(1) resolvePersona hits builtin by name, misses are null", async () => {
    const catalog = await loadPersonaCatalog(tmpRoot);
    expect(resolvePersona("explore", catalog)?.allowed_tools).toEqual([
      "FileRead",
      "Glob",
      "Grep",
    ]);
    expect(resolvePersona("nonexistent", catalog)).toBeNull();
    expect(resolvePersona("", catalog)).toBeNull();
  });

  it("(2) yaml override merges over builtin", async () => {
    const yaml = [
      "personas:",
      "  explore:",
      "    description: Custom explore",
      "    allowed_tools: [FileRead]",
      "    system_prompt: Custom system prompt for explore",
      "  custom_role:",
      "    description: User-defined role",
      "    allowed_tools: [Bash]",
      "    system_prompt: Do custom things",
      "",
    ].join("\n");
    await fs.writeFile(path.join(tmpRoot, "personas.yaml"), yaml);

    const catalog = await loadPersonaCatalog(tmpRoot);
    // explore overridden
    expect(catalog.explore?.description).toBe("Custom explore");
    expect(catalog.explore?.allowed_tools).toEqual(["FileRead"]);
    // coder/planner/reviewer retain builtins
    expect(catalog.coder?.allowed_tools).toBe(ALLOWED_TOOLS_WILDCARD);
    expect(catalog.planner).toBeDefined();
    expect(catalog.reviewer).toBeDefined();
    // new role added
    expect(catalog.custom_role?.allowed_tools).toEqual(["Bash"]);
  });

  it("(3) wildcard '*' allowed_tools round-trips through yaml", async () => {
    const yaml = [
      "personas:",
      "  mega:",
      "    description: all-tools role",
      "    allowed_tools: '*'",
      "    system_prompt: You can do anything",
      "",
    ].join("\n");
    await fs.writeFile(path.join(tmpRoot, "personas.yaml"), yaml);

    const catalog = await loadPersonaCatalog(tmpRoot);
    const spec = resolvePersona("mega", catalog);
    expect(spec).not.toBeNull();
    expect(spec?.allowed_tools).toBe(ALLOWED_TOOLS_WILDCARD);
  });

  it("(4) persona-with-explicit-allowed-tools: caller-level override is an integration concern (SpawnAgent), but the catalog still returns the preset verbatim", async () => {
    // The catalog never "knows" about caller-level overrides — that is
    // SpawnAgent's job (see SpawnAgent.test.ts). Here we just assert
    // the catalog returns the preset untouched so the integration can
    // decide precedence.
    const catalog = await loadPersonaCatalog(tmpRoot);
    const spec = resolvePersona("explore", catalog);
    expect(spec?.allowed_tools).toEqual(["FileRead", "Glob", "Grep"]);
    // Catalog is not mutated by lookup.
    const again = resolvePersona("explore", catalog);
    expect(again?.allowed_tools).toEqual(["FileRead", "Glob", "Grep"]);
  });

  it("(5) malformed entries fall back silently (missing required field)", async () => {
    const yaml = [
      "personas:",
      "  broken:",
      "    description: missing system_prompt",
      "    allowed_tools: [FileRead]",
      "  explore:",
      "    description: override OK",
      "    allowed_tools: [FileRead]",
      "    system_prompt: override",
      "",
    ].join("\n");
    await fs.writeFile(path.join(tmpRoot, "personas.yaml"), yaml);
    const catalog = await loadPersonaCatalog(tmpRoot);
    // broken entry was rejected
    expect(catalog.broken).toBeUndefined();
    // valid override applied
    expect(catalog.explore?.description).toBe("override OK");
  });

  it("(6) ENOENT personas.yaml returns builtin unchanged", async () => {
    const catalog = await loadPersonaCatalog(tmpRoot);
    expect(Object.keys(catalog).sort()).toEqual([
      "coder",
      "explore",
      "planner",
      "research",
      "reviewer",
      "scout",
      "synthesis",
    ]);
  });

  it("(6) malformed yaml syntax falls back to builtin", async () => {
    await fs.writeFile(
      path.join(tmpRoot, "personas.yaml"),
      "this: is: not: valid: yaml:\n  - at: all: [",
    );
    const catalog = await loadPersonaCatalog(tmpRoot);
    expect(catalog.explore).toEqual(BUILTIN_PERSONAS.explore);
  });
});
