/**
 * Persona catalog (T2-11, audit 02 proposal #3).
 *
 * Named subagent roles with preset tool filters and system-prompt
 * addenda. Callers invoke SpawnAgent with `persona: "explore"` and the
 * catalog expands to the preset's allowed_tools + system_prompt.
 *
 * Schema (workspace/personas.yaml):
 *
 *   personas:
 *     explore:
 *       description: "Read-only investigation"
 *       allowed_tools: [FileRead, Glob, Grep]
 *       allowed_skills: []
 *       system_prompt: "You are a read-only code exploration agent..."
 *     planner:
 *       description: "Plan mode — draft plans, cannot mutate"
 *       allowed_tools: [FileRead, Glob, Grep, TaskBoard, AskUserQuestion, ExitPlanMode]
 *       system_prompt: "You are a planning agent..."
 *     coder:
 *       description: "Full-access implementation"
 *       allowed_tools: "*"          # wildcard — expands to parent's full tool list
 *       system_prompt: "You implement code changes..."
 *     reviewer:
 *       description: "Read + annotation only"
 *       allowed_tools: [FileRead, Glob, Grep]
 *       system_prompt: "You are a code reviewer..."
 *
 * Lookup precedence (resolvePersona → SpawnAgent integration):
 *   1. catalog preset matched → expand allowed_tools + system_prompt
 *   2. caller `allowed_tools` explicit override wins over preset
 *   3. `allowed_tools: "*"` wildcard → parent's full tool list
 *   4. no match → free-form persona string (legacy behaviour)
 */

import fs from "node:fs/promises";
import path from "node:path";
import { parse as parseYaml } from "yaml";

/** "*" wildcard marker — expands to the parent's full tool list. */
export const ALLOWED_TOOLS_WILDCARD = "*" as const;

export interface PersonaSpec {
  description: string;
  /** Array of tool names, or the wildcard marker to inherit all parent tools. */
  allowed_tools: string[] | typeof ALLOWED_TOOLS_WILDCARD;
  allowed_skills?: string[];
  completion_contract?: PersonaCompletionContract;
  system_prompt: string;
}

export type PersonaCatalog = Record<string, PersonaSpec>;
export type PersonaEvidenceRequirement = "tool_call" | "files" | "artifact" | "text" | "none";

export interface PersonaCompletionContract {
  required_evidence?: PersonaEvidenceRequirement;
  required_files?: string[];
  require_non_empty_result?: boolean;
  reason?: string;
}

/**
 * Hard-coded fallback catalog. Used when `workspace/personas.yaml` is
 * absent. YAML overrides merge on top (by persona name).
 */
export const BUILTIN_PERSONAS: PersonaCatalog = {
  explore: {
    description: "Read-only investigation",
    allowed_tools: ["FileRead", "Glob", "Grep"],
    allowed_skills: [],
    system_prompt:
      "You are a read-only code exploration agent. Investigate the codebase to answer the parent agent's question. Do not attempt to modify any files; your toolset is read-only by design. Return a concise finding with file paths and relevant snippets.",
  },
  research: {
    description: "Web, KB, and local-source research with source evidence",
    allowed_tools: [
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
    ],
    allowed_skills: [],
    system_prompt:
      "You are a research agent. Inspect current, primary, and official sources with the available web, browser, KB, and local-read tools before answering. Prefer direct source inspection over search snippets, preserve contradictions, and return concise findings with source URLs or source identifiers for every factual claim.",
  },
  scout: {
    description: "Read-only external documentation and dependency reconnaissance",
    allowed_tools: [
      "WebSearch",
      "WebFetch",
      "Browser",
      "FileRead",
      "PackageDependencyResolve",
      "ExternalSourceCache",
      "ExternalSourceRead",
      "Glob",
      "Grep",
      "CodeSymbolSearch",
      "ArtifactRead",
      "Clock",
    ],
    allowed_skills: [],
    system_prompt:
      "You are a scout agent for external documentation, APIs, and dependency source research. Stay read-only. Prefer official docs, release notes, and upstream repositories; separate verified facts from inferences; return exact links, file paths, versions, and unresolved questions.",
  },
  synthesis: {
    description: "Answer composition from supplied evidence only",
    allowed_tools: [],
    allowed_skills: [],
    completion_contract: {
      required_evidence: "text",
      require_non_empty_result: true,
      reason: "synthesis persona composes from supplied source ledger/context without new tool use",
    },
    system_prompt:
      "You are a synthesis agent. Compose only from the source ledger, citations, excerpts, and context supplied by the parent. Do not introduce new factual claims from memory. Mark unsupported or conflicting claims as uncertain, and keep source attribution visible.",
  },
  planner: {
    description: "Plan mode — draft plans, cannot mutate",
    allowed_tools: [
      "FileRead",
      "Glob",
      "Grep",
      "TaskBoard",
      "AskUserQuestion",
      "ExitPlanMode",
    ],
    allowed_skills: [],
    system_prompt:
      "You are a planning agent. Draft an implementation plan for the parent agent's task. You cannot mutate files; use TaskBoard to structure the plan and ExitPlanMode when the plan is ready for review. Ask clarifying questions when requirements are ambiguous.",
  },
  coder: {
    description: "Full-access implementation",
    allowed_tools: ALLOWED_TOOLS_WILDCARD,
    system_prompt:
      "You implement code changes for the parent agent. You have access to the parent's full tool list. For TypeScript/JavaScript work, use CodeIntelligence for definition, references, hover, symbols, or diagnostics before editing existing code; fall back to CodeSymbolSearch only when semantic navigation is unavailable or the language is unsupported. Make the smallest correct change, verify by running tests or builds where applicable, and summarise the change on completion.",
  },
  reviewer: {
    description: "Read + annotation only",
    allowed_tools: ["FileRead", "Glob", "Grep"],
    allowed_skills: [],
    system_prompt:
      "You are a code reviewer. Examine the files or diff the parent agent points you at and report concerns by category: correctness, security, style, test coverage. You cannot mutate files; your output is a structured review.",
  },
};

interface RawPersonaYaml {
  personas?: Record<string, Partial<PersonaSpec> | undefined>;
}

const PERSONA_EVIDENCE_REQUIREMENTS = new Set<PersonaEvidenceRequirement>([
  "tool_call",
  "files",
  "artifact",
  "text",
  "none",
]);

function coerceCompletionContract(raw: unknown): PersonaCompletionContract | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const r = raw as Record<string, unknown>;
  const contract: PersonaCompletionContract = {};

  if (
    typeof r.required_evidence === "string" &&
    PERSONA_EVIDENCE_REQUIREMENTS.has(r.required_evidence as PersonaEvidenceRequirement)
  ) {
    contract.required_evidence = r.required_evidence as PersonaEvidenceRequirement;
  }
  if (
    Array.isArray(r.required_files) &&
    r.required_files.every((value) => typeof value === "string")
  ) {
    contract.required_files = r.required_files;
  }
  if (typeof r.require_non_empty_result === "boolean") {
    contract.require_non_empty_result = r.require_non_empty_result;
  }
  if (typeof r.reason === "string" && r.reason.length > 0) {
    contract.reason = r.reason;
  }

  return Object.keys(contract).length > 0 ? contract : undefined;
}

/**
 * Validate + normalise a raw YAML entry into a PersonaSpec. Returns
 * null if the entry is malformed (missing required fields). We do not
 * throw — a bad entry in user YAML silently falls back to the builtin
 * preset (if any) or is ignored.
 */
function coercePersonaSpec(raw: unknown): PersonaSpec | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;

  const description = typeof r.description === "string" ? r.description : null;
  const systemPrompt =
    typeof r.system_prompt === "string" ? r.system_prompt : null;
  if (description === null || systemPrompt === null) return null;

  let allowedTools: PersonaSpec["allowed_tools"];
  if (r.allowed_tools === ALLOWED_TOOLS_WILDCARD) {
    allowedTools = ALLOWED_TOOLS_WILDCARD;
  } else if (
    Array.isArray(r.allowed_tools) &&
    r.allowed_tools.every((x) => typeof x === "string")
  ) {
    allowedTools = r.allowed_tools as string[];
  } else {
    return null;
  }

  let allowedSkills: string[] | undefined;
  if (Array.isArray(r.allowed_skills)) {
    const strs = r.allowed_skills.filter((x): x is string => typeof x === "string");
    allowedSkills = strs;
  }

  const spec: PersonaSpec = {
    description,
    allowed_tools: allowedTools,
    system_prompt: systemPrompt,
  };
  if (allowedSkills !== undefined) spec.allowed_skills = allowedSkills;
  const completionContract = coerceCompletionContract(r.completion_contract);
  if (completionContract !== undefined) spec.completion_contract = completionContract;
  return spec;
}

/**
 * Load `workspace/personas.yaml` if present; merge over BUILTIN_PERSONAS
 * by persona name. Missing file → builtin only. Malformed file → builtin
 * only (errors are swallowed by design: catalog resolution is a soft
 * feature, never a hard failure).
 */
export async function loadPersonaCatalog(
  workspaceRoot: string,
): Promise<PersonaCatalog> {
  const yamlPath = path.join(workspaceRoot, "personas.yaml");
  let raw: string;
  try {
    raw = await fs.readFile(yamlPath, "utf8");
  } catch (err) {
    const code =
      err && typeof err === "object" && "code" in err
        ? (err as { code?: unknown }).code
        : undefined;
    if (code === "ENOENT") return { ...BUILTIN_PERSONAS };
    // Other read errors — treat as absent, don't crash the agent.
    return { ...BUILTIN_PERSONAS };
  }

  let parsed: RawPersonaYaml;
  try {
    parsed = parseYaml(raw) as RawPersonaYaml;
  } catch {
    return { ...BUILTIN_PERSONAS };
  }

  const merged: PersonaCatalog = { ...BUILTIN_PERSONAS };
  const userPersonas = parsed?.personas;
  if (userPersonas && typeof userPersonas === "object") {
    for (const [name, entry] of Object.entries(userPersonas)) {
      const spec = coercePersonaSpec(entry);
      if (spec !== null) merged[name] = spec;
    }
  }
  return merged;
}

/**
 * Look up a persona by name. Returns null when the name is not a
 * catalog entry — caller should fall back to treating `persona` as a
 * free-form label (legacy behaviour).
 */
export function resolvePersona(
  name: string,
  catalog: PersonaCatalog,
): PersonaSpec | null {
  if (!name) return null;
  const entry = catalog[name];
  return entry ?? null;
}
