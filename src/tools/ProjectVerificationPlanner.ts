import fs from "node:fs/promises";
import path from "node:path";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { Workspace } from "../storage/Workspace.js";
import { errorResult } from "../util/toolResult.js";

export interface ProjectVerificationPlannerInput {
  projectPath?: string;
}

export type ProjectVerificationKind =
  | "test"
  | "lint"
  | "typecheck"
  | "build"
  | "compile";

export interface ProjectVerificationCommand {
  kind: ProjectVerificationKind;
  command: string;
  cwd: string;
  runner: "TestRun";
  confidence: "high" | "medium" | "low";
  reason: string;
}

export interface ProjectVerificationPlannerOutput {
  cwd: string;
  projectTypes: string[];
  commands: ProjectVerificationCommand[];
  warnings: string[];
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    projectPath: {
      type: "string",
      description: "Workspace-relative project directory to inspect. Default: workspace root.",
    },
  },
} as const;

const SCRIPT_ORDER: Array<{
  script: string;
  kind: ProjectVerificationKind;
  commandName: string;
}> = [
  { script: "test", kind: "test", commandName: "test" },
  { script: "lint", kind: "lint", commandName: "run lint" },
  { script: "typecheck", kind: "typecheck", commandName: "run typecheck" },
  { script: "build", kind: "build", commandName: "run build" },
];

async function exists(filePath: string): Promise<boolean> {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

async function readText(filePath: string): Promise<string | null> {
  try {
    return await fs.readFile(filePath, "utf8");
  } catch {
    return null;
  }
}

function workspaceRelative(root: string, target: string): string {
  const rel = path.relative(root, target);
  return rel.length === 0 ? "." : rel;
}

async function detectPackageManager(cwd: string): Promise<"npm" | "pnpm" | "yarn" | "bun"> {
  if (await exists(path.join(cwd, "pnpm-lock.yaml"))) return "pnpm";
  if (await exists(path.join(cwd, "yarn.lock"))) return "yarn";
  if ((await exists(path.join(cwd, "bun.lockb"))) || (await exists(path.join(cwd, "bun.lock")))) {
    return "bun";
  }
  return "npm";
}

function packageManagerCommand(
  manager: "npm" | "pnpm" | "yarn" | "bun",
  commandName: string,
): string {
  if (manager === "npm") return `npm ${commandName}`;
  if (manager === "pnpm") return `pnpm ${commandName.replace(/^run /, "")}`;
  if (manager === "yarn") return `yarn ${commandName.replace(/^run /, "")}`;
  return `bun ${commandName.replace(/^run /, "")}`;
}

function addCommand(
  commands: ProjectVerificationCommand[],
  input: Omit<ProjectVerificationCommand, "runner" | "confidence"> & {
    confidence?: "high" | "medium" | "low";
  },
): void {
  if (commands.some((command) => command.command === input.command && command.cwd === input.cwd)) {
    return;
  }
  commands.push({
    ...input,
    runner: "TestRun",
    confidence: input.confidence ?? "high",
  });
}

async function addNodeCommands(input: {
  cwd: string;
  cwdRel: string;
  projectTypes: Set<string>;
  commands: ProjectVerificationCommand[];
  warnings: string[];
}): Promise<void> {
  const packageJsonPath = path.join(input.cwd, "package.json");
  const raw = await readText(packageJsonPath);
  if (raw === null) return;
  input.projectTypes.add("node");

  let parsed: { scripts?: Record<string, unknown> };
  try {
    parsed = JSON.parse(raw) as { scripts?: Record<string, unknown> };
  } catch {
    input.warnings.push("package.json could not be parsed; skipped npm script inference");
    return;
  }

  const scripts = parsed.scripts ?? {};
  const manager = await detectPackageManager(input.cwd);
  for (const item of SCRIPT_ORDER) {
    if (typeof scripts[item.script] !== "string") continue;
    addCommand(input.commands, {
      kind: item.kind,
      command: packageManagerCommand(manager, item.commandName),
      cwd: input.cwdRel,
      reason: `package.json defines scripts.${item.script}`,
    });
  }
}

async function addTypeScriptCommands(input: {
  cwd: string;
  cwdRel: string;
  projectTypes: Set<string>;
  commands: ProjectVerificationCommand[];
}): Promise<void> {
  if (!(await exists(path.join(input.cwd, "tsconfig.json")))) return;
  input.projectTypes.add("typescript");
  if (input.commands.some((command) => command.kind === "typecheck")) return;
  addCommand(input.commands, {
    kind: "typecheck",
    command: "npx tsc --noEmit --pretty false",
    cwd: input.cwdRel,
    confidence: "medium",
    reason: "tsconfig.json exists but no typecheck script was found",
  });
}

async function addPythonCommands(input: {
  cwd: string;
  cwdRel: string;
  projectTypes: Set<string>;
  commands: ProjectVerificationCommand[];
}): Promise<void> {
  const pyproject = await readText(path.join(input.cwd, "pyproject.toml"));
  const requirements = await readText(path.join(input.cwd, "requirements.txt"));
  if (pyproject === null && requirements === null) return;
  input.projectTypes.add("python");
  const hasPytest =
    (pyproject?.toLowerCase().includes("pytest") ?? false) ||
    (requirements?.toLowerCase().includes("pytest") ?? false);
  if (hasPytest) {
    addCommand(input.commands, {
      kind: "test",
      command: "python -m pytest",
      cwd: input.cwdRel,
      reason: "Python project metadata references pytest",
    });
  }
  addCommand(input.commands, {
    kind: "compile",
    command: "python -m compileall .",
    cwd: input.cwdRel,
    confidence: "medium",
    reason: "Python project metadata exists",
  });
}

async function addGoCommands(input: {
  cwd: string;
  cwdRel: string;
  projectTypes: Set<string>;
  commands: ProjectVerificationCommand[];
}): Promise<void> {
  if (!(await exists(path.join(input.cwd, "go.mod")))) return;
  input.projectTypes.add("go");
  addCommand(input.commands, {
    kind: "test",
    command: "go test ./...",
    cwd: input.cwdRel,
    reason: "go.mod exists",
  });
}

async function addRustCommands(input: {
  cwd: string;
  cwdRel: string;
  projectTypes: Set<string>;
  commands: ProjectVerificationCommand[];
}): Promise<void> {
  if (!(await exists(path.join(input.cwd, "Cargo.toml")))) return;
  input.projectTypes.add("rust");
  addCommand(input.commands, {
    kind: "test",
    command: "cargo test",
    cwd: input.cwdRel,
    reason: "Cargo.toml exists",
  });
  addCommand(input.commands, {
    kind: "compile",
    command: "cargo check",
    cwd: input.cwdRel,
    reason: "Cargo.toml exists",
  });
}

export function makeProjectVerificationPlannerTool(
  workspaceRoot: string,
): Tool<ProjectVerificationPlannerInput, ProjectVerificationPlannerOutput> {
  const defaultWorkspace = new Workspace(workspaceRoot);
  return {
    name: "ProjectVerificationPlanner",
    description:
      "Inspect project metadata and recommend deterministic TestRun commands for coding work. Use before final verification to choose tests, lint, typecheck, build, or language-native checks instead of guessing commands.",
    inputSchema: INPUT_SCHEMA,
    permission: "read",
    kind: "core",
    mutatesWorkspace: false,
    isConcurrencySafe: true,
    validate(input) {
      if (!input) return null;
      if (input.projectPath !== undefined && typeof input.projectPath !== "string") {
        return "`projectPath` must be a string";
      }
      return null;
    },
    async execute(
      input: ProjectVerificationPlannerInput,
      ctx: ToolContext,
    ): Promise<ToolResult<ProjectVerificationPlannerOutput>> {
      const start = Date.now();
      try {
        const ws = ctx.spawnWorkspace ?? defaultWorkspace;
        const cwd = input.projectPath ? ws.resolve(input.projectPath) : ws.root;
        const cwdRel = workspaceRelative(ws.root, cwd);
        const projectTypes = new Set<string>();
        const commands: ProjectVerificationCommand[] = [];
        const warnings: string[] = [];

        await addNodeCommands({ cwd, cwdRel, projectTypes, commands, warnings });
        await addTypeScriptCommands({ cwd, cwdRel, projectTypes, commands });
        await addPythonCommands({ cwd, cwdRel, projectTypes, commands });
        await addGoCommands({ cwd, cwdRel, projectTypes, commands });
        await addRustCommands({ cwd, cwdRel, projectTypes, commands });

        if (commands.length === 0) {
          warnings.push("No known project metadata found; inspect repository docs before choosing TestRun commands.");
        }

        const output: ProjectVerificationPlannerOutput = {
          cwd: cwdRel,
          projectTypes: [...projectTypes],
          commands,
          warnings,
        };

        return {
          status: "ok",
          output,
          durationMs: Date.now() - start,
          metadata: {
            evidenceKind: "verification_plan",
            commandCount: commands.length,
            projectTypes: output.projectTypes,
          },
        };
      } catch (err) {
        return errorResult(err, start);
      }
    },
  };
}
