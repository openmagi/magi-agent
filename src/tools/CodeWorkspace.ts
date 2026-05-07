import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { Workspace } from "../storage/Workspace.js";
import { errorResult } from "../util/toolResult.js";

const execFileAsync = promisify(execFile);

export interface CodeWorkspaceInput {
  projectName?: string;
  initializeGit?: boolean;
}

export interface CodeWorkspaceOutput {
  relativePath: string;
  absolutePath: string;
  created: boolean;
  gitInitialized: boolean;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    projectName: {
      type: "string",
      description: "Human-readable project/repo name. Slugged under workspace/code/.",
    },
    initializeGit: {
      type: "boolean",
      default: true,
      description: "Initialize the project folder as a git repository. Default true.",
    },
  },
} as const;

function slugifyProjectName(input: string | undefined): string {
  const raw = (input ?? "project").trim().toLowerCase();
  const slug = raw
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/\.{2,}/g, ".");
  return slug.length > 0 ? slug.slice(0, 80) : "project";
}

async function exists(target: string): Promise<boolean> {
  try {
    await fs.access(target);
    return true;
  } catch {
    return false;
  }
}

export function makeCodeWorkspaceTool(
  workspaceRoot: string,
): Tool<CodeWorkspaceInput, CodeWorkspaceOutput> {
  const defaultWorkspace = new Workspace(workspaceRoot);
  return {
    name: "CodeWorkspace",
    description:
      "Create or reuse a dedicated git repo folder for coding work under workspace/code/<project>. Use this before writing source files for a coding task so code, tests, and generated outputs do not scatter in the workspace root.",
    inputSchema: INPUT_SCHEMA,
    permission: "write",
    mutatesWorkspace: true,
    isConcurrencySafe: false,
    validate(input) {
      if (input?.projectName !== undefined && typeof input.projectName !== "string") {
        return "`projectName` must be a string";
      }
      if (input?.initializeGit !== undefined && typeof input.initializeGit !== "boolean") {
        return "`initializeGit` must be a boolean";
      }
      return null;
    },
    async execute(
      input: CodeWorkspaceInput,
      ctx: ToolContext,
    ): Promise<ToolResult<CodeWorkspaceOutput>> {
      const start = Date.now();
      try {
        const ws = ctx.spawnWorkspace ?? defaultWorkspace;
        const relativePath = path.posix.join("code", slugifyProjectName(input.projectName));
        const absolutePath = ws.resolve(relativePath);
        const created = !(await exists(absolutePath));
        await fs.mkdir(absolutePath, { recursive: true });

        const shouldInitGit = input.initializeGit !== false;
        const gitDir = path.join(absolutePath, ".git");
        let gitInitialized = await exists(gitDir);
        if (shouldInitGit && !gitInitialized) {
          await execFileAsync("git", ["init", "-q"], { cwd: absolutePath });
          gitInitialized = await exists(gitDir);
        }

        return {
          status: "ok",
          output: {
            relativePath,
            absolutePath,
            created,
            gitInitialized,
          },
          durationMs: Date.now() - start,
          metadata: {
            codeWorkspace: true,
            relativePath,
          },
        };
      } catch (err) {
        return errorResult(err, start);
      }
    },
  };
}
