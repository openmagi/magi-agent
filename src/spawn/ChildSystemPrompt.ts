import fs from "node:fs/promises";
import path from "node:path";
import {
  buildRuntimePolicyBlock,
  PolicyKernel,
} from "../policy/PolicyKernel.js";
import {
  AGENT_SELF_MODEL_BLOCK,
  EXECUTION_DISCIPLINE_POLICY,
  OUTPUT_RULES_BLOCK,
  RUNTIME_EVIDENCE_POLICY,
  SUBAGENT_EXECUTION_BASELINE_BLOCK,
} from "../prompt/RuntimePromptBlocks.js";
import { Workspace } from "../storage/Workspace.js";
import { isReliabilityPromptEnabled } from "../hooks/builtin/reliabilityPromptInjector.js";

export interface ChildSystemPromptInput {
  persona: string;
  prompt: string;
  parentTurnId: string;
  parentSpawnDepth: number;
  parentWorkspaceRoot: string;
  spawnDir: string;
  workspacePolicy: "trusted" | "isolated";
}

type ChildContextDoc = {
  relPath: string;
  transform?: (body: string) => string;
};

const MAX_CHILD_CONTEXT_DOC_CHARS = 12_000;

const CHILD_CONTEXT_DOCS: readonly ChildContextDoc[] = [
  { relPath: "CLAUDE.md" },
  { relPath: "AGENTS.md", transform: renderAgentsForChild },
  { relPath: "BOOTSTRAP.md" },
  { relPath: "IDENTITY.md" },
  { relPath: "USER.md" },
  { relPath: "MEMORY.md" },
  { relPath: "memory/ROOT.md" },
  { relPath: "SCRATCHPAD.md" },
  { relPath: "WORKING.md" },
  { relPath: "LEARNING.md" },
  { relPath: "TOOLS.md" },
  { relPath: "EXECUTION.md", transform: renderExecutionForChild },
  { relPath: "DISCIPLINE.md" },
  { relPath: "EXECUTION-TOOLS.md", transform: renderExecutionToolsForChild },
];

function truncateDoc(body: string): string {
  if (body.length <= MAX_CHILD_CONTEXT_DOC_CHARS) return body.trim();
  return `${body.slice(0, MAX_CHILD_CONTEXT_DOC_CHARS).trimEnd()}\n\n[truncated for child system prompt]`;
}

function extractMarkdownSection(markdown: string, heading: string): string | null {
  const lines = markdown.split(/\r?\n/);
  const start = lines.findIndex((line) => line.trim() === `## ${heading}`);
  if (start < 0) return null;
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    if (/^##\s+/.test(lines[i] ?? "")) {
      end = i;
      break;
    }
  }
  return lines.slice(start, end).join("\n").trim();
}

function stripMarkdownSections(
  markdown: string,
  shouldStripHeading: (heading: string) => boolean,
): string {
  const lines = markdown.split(/\r?\n/);
  const kept: string[] = [];
  let stripping = false;
  for (const line of lines) {
    const heading = line.match(/^##\s+(.+?)\s*$/);
    if (heading) {
      stripping = shouldStripHeading(heading[1] ?? "");
    }
    if (!stripping) kept.push(line);
  }
  return kept.join("\n").trim();
}

function renderAgentsForChild(body: string): string {
  const sections: string[] = [];
  const hipocampus = body.match(
    /<!-- hipocampus:protocol:start -->([\s\S]*?)<!-- hipocampus:protocol:end -->/,
  );
  if (hipocampus) sections.push(hipocampus[0].trim());

  for (const heading of [
    "Runtime Environment",
    "Temporal Awareness",
    "File Permissions",
  ]) {
    const section = extractMarkdownSection(body, heading);
    if (section) sections.push(section);
  }

  if (sections.length === 0) {
    if (/(?:meta-layer|Subagent Dispatch|agent-run\.sh|Native SpawnAgent)/i.test(body)) {
      return "<!-- AGENTS.md parent meta-layer content omitted for spawned child execution. -->";
    }
    return body;
  }
  sections.push(
    "<!-- Parent meta-layer dispatch/orchestration sections intentionally omitted for spawned child execution. -->",
  );
  return sections.join("\n\n");
}

function renderExecutionForChild(body: string): string {
  return stripMarkdownSections(body, (heading) =>
    /^(?:Multi-Agent Orchestration)$/i.test(heading),
  );
}

function renderExecutionToolsForChild(body: string): string {
  return stripMarkdownSections(body, (heading) =>
    /^(?:Agent Runner|Coding Agent)\b/i.test(heading),
  );
}

async function readChildContextDoc(
  root: string,
  doc: ChildContextDoc,
): Promise<string | null> {
  const rootResolved = path.resolve(root);
  const full = path.resolve(rootResolved, doc.relPath);
  if (!full.startsWith(rootResolved + path.sep) && full !== rootResolved) {
    return null;
  }
  try {
    const raw = await fs.readFile(full, "utf8");
    const transformed = doc.transform ? doc.transform(raw) : raw;
    if (!transformed.trim()) return null;
    return `# ${doc.relPath}\n\n${truncateDoc(transformed)}`;
  } catch {
    return null;
  }
}

async function renderChildWorkspaceContext(parentWorkspaceRoot: string): Promise<string> {
  const docs = await Promise.all(
    CHILD_CONTEXT_DOCS.map((doc) => readChildContextDoc(parentWorkspaceRoot, doc)),
  );
  const body = docs.filter((doc): doc is string => doc !== null).join("\n\n---\n\n");
  if (!body) return "";
  return [
    '<subagent_workspace_context source="parent-workspace">',
    "The following workspace documents are injected for the spawned child agent. They are context, not permission to act as the parent meta-layer.",
    "",
    body,
    "</subagent_workspace_context>",
  ].join("\n");
}

async function renderChildRuntimePolicy(parentWorkspaceRoot: string): Promise<string> {
  try {
    const workspace = new Workspace(parentWorkspaceRoot);
    const snapshot = await new PolicyKernel(workspace).current();
    return buildRuntimePolicyBlock(snapshot);
  } catch {
    return "";
  }
}

function renderChildRuntimeContract(input: ChildSystemPromptInput): string {
  if (input.workspacePolicy === "isolated") {
    return [
      "<subagent-runtime-contract>",
      "You are an isolated child worker scoped to the .spawn scratch workspace for this task.",
      "Do not assume access to parent workspace files unless the parent prompt provides them through tools or copied inputs.",
      "Write requested deliverables inside your .spawn workspace and return a non-empty final status summary.",
      "</subagent-runtime-contract>",
    ].join("\n");
  }
  return [
    "<subagent-runtime-contract>",
    "You are a trusted worker for the same bot owner, with the same workspace authority as the parent agent.",
    "Use the parent workspace for requested deliverables and durable edits.",
    "Use .spawn only for scratch notes, temporary files, audit breadcrumbs, or attempt-local recovery data.",
    "You do not inherit the full conversation unless the parent includes it in the task prompt.",
    "Return a non-empty final status summary describing what changed and any remaining risk.",
    "</subagent-runtime-contract>",
  ].join("\n");
}

export async function buildChildSystemPrompt(
  input: ChildSystemPromptInput,
): Promise<string> {
  const [workspaceContext, runtimePolicy] = await Promise.all([
    renderChildWorkspaceContext(input.parentWorkspaceRoot),
    renderChildRuntimePolicy(input.parentWorkspaceRoot),
  ]);
  const reliabilityBlocks = isReliabilityPromptEnabled()
    ? [RUNTIME_EVIDENCE_POLICY, EXECUTION_DISCIPLINE_POLICY]
    : [];

  return [
    AGENT_SELF_MODEL_BLOCK,
    ...reliabilityBlocks,
    OUTPUT_RULES_BLOCK,
    workspaceContext,
    runtimePolicy,
    `[Persona: ${input.persona}]`,
    `[Spawn: parent=${input.parentTurnId} depth=${input.parentSpawnDepth + 1}]`,
    `[Workspace: ${input.spawnDir}]`,
    renderChildRuntimeContract(input),
    "",
    SUBAGENT_EXECUTION_BASELINE_BLOCK,
    "<subagent_role_override>",
    "You are the spawned child agent, not the main meta-layer agent.",
    "If injected workspace documents describe parent/meta-agent orchestration, treat those parts as parent-only background, not as your role.",
    "Execute the delegated work directly, within the child workspace/tool boundary, and return concise evidence/results to the parent.",
    "</subagent_role_override>",
  ]
    .filter((part) => part.trim().length > 0)
    .join("\n\n");
}
