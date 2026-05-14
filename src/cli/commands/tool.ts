/**
 * CLI `magi tool` subcommands — create, list, enable, disable, test,
 * logs.
 *
 * Wired into the main CLI router via `src/cli/index.ts`.
 */

import fs from "node:fs";
import path from "node:path";

import { parse as parseYaml, stringify as stringifyYaml } from "yaml";
import { loadMagiConfig, magiConfigPath } from "../../config/MagiConfig.js";
import { loadUserTools } from "../../tools/ToolLoader.js";
import { ToolLogger } from "../../tools/ToolLogger.js";
import type { PermissionClass } from "../../Tool.js";

/* ------------------------------------------------------------------ */
/*  Formatting helpers                                                 */
/* ------------------------------------------------------------------ */

const BOLD = "\x1b[1m";
const DIM = "\x1b[2m";
const GREEN = "\x1b[32m";
const RED = "\x1b[31m";
const YELLOW = "\x1b[33m";
const RESET = "\x1b[0m";

function padRight(str: string, len: number): string {
  return str.length >= len ? str : str + " ".repeat(len - str.length);
}

/* ------------------------------------------------------------------ */
/*  Valid permissions (for CLI validation)                              */
/* ------------------------------------------------------------------ */

const VALID_PERMISSIONS: ReadonlySet<string> = new Set([
  "read",
  "write",
  "execute",
  "net",
  "meta",
]);

/* ------------------------------------------------------------------ */
/*  `magi tool create <name> --permission <perm>`                      */
/* ------------------------------------------------------------------ */

function toPascalCase(str: string): string {
  return str
    .split(/[-_\s]+/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join("");
}

export async function toolCreate(args: string[]): Promise<void> {
  const name = args[0];
  if (!name) {
    console.error(
      "Usage: magi tool create <name> --permission <read|write|execute|net|meta>",
    );
    process.exitCode = 1;
    return;
  }

  const permIdx = args.indexOf("--permission");
  const permission = permIdx !== -1 ? args[permIdx + 1] : undefined;
  if (!permission || !VALID_PERMISSIONS.has(permission)) {
    console.error(
      `Error: --permission must be one of: ${[...VALID_PERMISSIONS].join(", ")}`,
    );
    process.exitCode = 1;
    return;
  }

  const toolsDir = path.resolve(process.cwd(), "tools");
  const toolDir = path.join(toolsDir, name);
  const fixturesDir = path.join(toolDir, "__fixtures__");
  const indexFile = path.join(toolDir, "index.ts");

  if (fs.existsSync(indexFile)) {
    console.error(`Error: Tool already exists: ${indexFile}`);
    process.exitCode = 1;
    return;
  }

  const pascalName = toPascalCase(name);

  const indexContent = `import type { Tool, ToolContext, ToolResult } from "../../src/Tool.js";

interface ${pascalName}Input {
  query: string;
}

export function make${pascalName}Tool(): Tool<${pascalName}Input> {
  return {
    name: "${pascalName}",
    description: "TODO: describe your tool",
    permission: "${permission}" as const,
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", description: "TODO: describe input" },
      },
      required: ["query"],
      additionalProperties: false,
    },
    async execute(input: ${pascalName}Input, ctx: ToolContext): Promise<ToolResult> {
      const startMs = Date.now();
      // TODO: implement
      return { status: "ok", output: {}, durationMs: Date.now() - startMs };
    },
  };
}
`;

  const toolMdContent = `---
name: ${pascalName}
permission: ${permission}
description: "TODO: describe your tool"
version: "0.1.0"
---

# ${pascalName}

TODO: Document your tool here.
`;

  const fixtureContent = `# ${pascalName} — basic test fixture
#
# Run with: magi tool test ${name}

description: "Basic ${pascalName} test case"
input:
  query: "test query"
expected:
  status: "ok"
`;

  fs.mkdirSync(fixturesDir, { recursive: true });
  fs.writeFileSync(indexFile, indexContent, "utf-8");
  fs.writeFileSync(path.join(toolDir, "TOOL.md"), toolMdContent, "utf-8");
  fs.writeFileSync(
    path.join(fixturesDir, "basic.yaml"),
    fixtureContent,
    "utf-8",
  );

  console.log(`${GREEN}Created tool:${RESET} ${indexFile}`);
  console.log(`${GREEN}Created metadata:${RESET} ${toolDir}/TOOL.md`);
  console.log(
    `${GREEN}Created fixture:${RESET} ${fixturesDir}/basic.yaml`,
  );
}

/* ------------------------------------------------------------------ */
/*  `magi tool list`                                                   */
/* ------------------------------------------------------------------ */

export async function toolList(): Promise<void> {
  const config = loadMagiConfig();
  const { tools: userTools, warnings } = await loadUserTools({
    directory: config.tools.directory,
    globalDirectory: config.tools.global_directory,
  });

  for (const w of warnings) {
    console.warn(`${YELLOW}warn:${RESET} ${w}`);
  }

  interface ToolRow {
    name: string;
    permission: string;
    source: string;
    enabled: boolean;
    dangerous: boolean;
  }

  const rows: ToolRow[] = [];

  for (const t of userTools) {
    const override = config.tools.overrides[t.name];
    rows.push({
      name: t.name,
      permission: override?.permission ?? t.permission,
      source: "user",
      enabled: override?.enabled !== false,
      dangerous: t.dangerous ?? false,
    });
  }

  if (rows.length === 0) {
    console.log(`${DIM}No custom tools found.${RESET}`);
    console.log(
      `${DIM}Create one with: magi tool create <name> --permission <perm>${RESET}`,
    );
    return;
  }

  const nameWidth = Math.max(20, ...rows.map((r) => r.name.length + 2));
  const permWidth = Math.max(12, ...rows.map((r) => r.permission.length + 2));

  console.log(
    `${BOLD}${padRight("NAME", nameWidth)}${padRight("PERMISSION", permWidth)}${padRight("SOURCE", 14)}${padRight("DANGER", 10)}STATUS${RESET}`,
  );
  console.log("-".repeat(nameWidth + permWidth + 14 + 10 + 10));

  for (const r of rows) {
    const status = r.enabled
      ? `${GREEN}enabled${RESET}`
      : `${RED}disabled${RESET}`;
    const danger = r.dangerous ? `${RED}yes${RESET}` : "no";
    console.log(
      `${padRight(r.name, nameWidth)}${padRight(r.permission, permWidth)}${padRight(r.source, 14)}${padRight(danger, 10)}${status}`,
    );
  }
}

/* ------------------------------------------------------------------ */
/*  `magi tool enable <name>` / `magi tool disable <name>`             */
/* ------------------------------------------------------------------ */

export async function toolToggle(
  name: string,
  enable: boolean,
): Promise<void> {
  if (!name) {
    console.error(
      `Usage: magi tool ${enable ? "enable" : "disable"} <name>`,
    );
    process.exitCode = 1;
    return;
  }

  const configPath = magiConfigPath();
  let doc: Record<string, unknown> = {};

  if (fs.existsSync(configPath)) {
    try {
      const raw = fs.readFileSync(configPath, "utf-8");
      doc = (parseYaml(raw) as Record<string, unknown>) ?? {};
    } catch {
      doc = {};
    }
  }

  if (!doc.tools || typeof doc.tools !== "object") {
    doc.tools = {};
  }
  const tools = doc.tools as Record<string, unknown>;
  if (!tools.overrides || typeof tools.overrides !== "object") {
    tools.overrides = {};
  }
  const overrides = tools.overrides as Record<
    string,
    Record<string, unknown>
  >;

  if (!overrides[name]) {
    overrides[name] = {};
  }
  overrides[name].enabled = enable;

  fs.writeFileSync(configPath, stringifyYaml(doc), "utf-8");

  const verb = enable ? "enabled" : "disabled";
  console.log(
    `${GREEN}Tool "${name}" ${verb}.${RESET} Updated ${configPath}`,
  );
}

/* ------------------------------------------------------------------ */
/*  `magi tool test [name] [--all]`                                    */
/* ------------------------------------------------------------------ */

export async function toolTest(args: string[]): Promise<void> {
  const runAll = args.includes("--all");
  const nameArg = args.find((a) => !a.startsWith("--"));

  const config = loadMagiConfig();
  const { tools, warnings } = await loadUserTools({
    directory: config.tools.directory,
    globalDirectory: config.tools.global_directory,
  });

  for (const w of warnings) {
    console.warn(`${YELLOW}warn:${RESET} ${w}`);
  }

  const targets = nameArg
    ? tools.filter((t) => t.name === nameArg)
    : runAll
      ? tools
      : [];

  if (targets.length === 0) {
    if (nameArg) {
      console.error(`Tool "${nameArg}" not found.`);
    } else {
      console.error(
        "Usage: magi tool test <name> or magi tool test --all",
      );
    }
    process.exitCode = 1;
    return;
  }

  const fixturesBase = path.resolve(process.cwd(), "tools");
  let passed = 0;
  let failed = 0;

  for (const tool of targets) {
    // Look for fixtures in tool dir or common fixtures dir
    const toolFixtureDir = path.join(
      fixturesBase,
      tool.name.toLowerCase(),
      "__fixtures__",
    );
    if (!fs.existsSync(toolFixtureDir)) {
      console.log(
        `${YELLOW}skip${RESET} ${tool.name} — no fixtures at ${toolFixtureDir}`,
      );
      continue;
    }

    const fixtureFiles = fs
      .readdirSync(toolFixtureDir)
      .filter((f) => f.endsWith(".yaml") || f.endsWith(".yml"));

    for (const file of fixtureFiles) {
      const fixturePath = path.join(toolFixtureDir, file);
      try {
        const raw = fs.readFileSync(fixturePath, "utf-8");
        const fixture = parseYaml(raw) as Record<string, unknown>;

        const mockCtx = {
          botId: "test",
          sessionKey: "test",
          turnId: "test",
          workspaceRoot: process.cwd(),
          askUser: async () => ({}),
          emitProgress: () => {},
          abortSignal: new AbortController().signal,
          staging: {
            stageFileWrite: () => {},
            stageTranscriptAppend: () => {},
            stageAuditEvent: () => {},
          },
        };

        const result = await tool.execute(
          fixture.input as never,
          mockCtx,
        );

        const expected = fixture.expected as
          | Record<string, unknown>
          | undefined;
        if (expected?.status && result) {
          if (result.status !== expected.status) {
            console.log(
              `${RED}FAIL${RESET} ${tool.name}/${file}: expected status="${String(expected.status)}", got="${result.status}"`,
            );
            failed++;
            continue;
          }
        }

        console.log(`${GREEN}PASS${RESET} ${tool.name}/${file}`);
        passed++;
      } catch (err) {
        console.log(
          `${RED}FAIL${RESET} ${tool.name}/${file}: ${(err as Error).message}`,
        );
        failed++;
      }
    }
  }

  console.log(
    `\n${passed} passed, ${failed} failed, ${passed + failed} total`,
  );
  if (failed > 0) process.exitCode = 1;
}

/* ------------------------------------------------------------------ */
/*  `magi tool logs <name> [--since <date>] [--limit <n>]`             */
/* ------------------------------------------------------------------ */

export async function toolLogs(args: string[]): Promise<void> {
  const name = args.find((a) => !a.startsWith("--"));
  if (!name) {
    console.error(
      "Usage: magi tool logs <name> [--since <date>] [--limit <n>]",
    );
    process.exitCode = 1;
    return;
  }

  const sinceIdx = args.indexOf("--since");
  const sinceStr = sinceIdx !== -1 ? args[sinceIdx + 1] : undefined;
  const since = sinceStr ? new Date(sinceStr) : undefined;

  const limitIdx = args.indexOf("--limit");
  const limitStr = limitIdx !== -1 ? args[limitIdx + 1] : undefined;
  const limit = limitStr ? parseInt(limitStr, 10) : undefined;

  const logger = new ToolLogger();
  const entries = logger.getLogs(name, { since, limit });

  if (entries.length === 0) {
    console.log(`${DIM}No log entries for "${name}".${RESET}`);
    return;
  }

  for (const entry of entries) {
    const ts = entry.timestamp;
    const dur = `${entry.durationMs}ms`;
    const err = entry.error ? ` ${RED}error=${entry.error}${RESET}` : "";
    const preview = entry.inputPreview
      ? ` ${DIM}input=${entry.inputPreview}${RESET}`
      : "";
    console.log(
      `${DIM}${ts}${RESET} ${entry.toolName} status=${entry.status} ${dur}${err}${preview}`,
    );
  }
}

/* ------------------------------------------------------------------ */
/*  Router                                                             */
/* ------------------------------------------------------------------ */

export async function runToolCommand(args: string[]): Promise<void> {
  const subcommand = args[0];
  const rest = args.slice(1);

  switch (subcommand) {
    case "create":
      await toolCreate(rest);
      break;
    case "list":
      await toolList();
      break;
    case "enable":
      await toolToggle(rest[0] ?? "", true);
      break;
    case "disable":
      await toolToggle(rest[0] ?? "", false);
      break;
    case "test":
      await toolTest(rest);
      break;
    case "logs":
      await toolLogs(rest);
      break;
    default:
      console.log(`
${BOLD}magi tool${RESET} — manage custom tools

${BOLD}Subcommands:${RESET}
  create <name> --permission <perm>   Scaffold a new tool + fixture
  list                                List all custom tools
  enable <name>                       Enable a tool in magi.config.yaml
  disable <name>                      Disable a tool in magi.config.yaml
  test [name] [--all]                 Run tool fixtures
  logs <name> [--since <date>] [--limit <n>]  View tool execution logs
`);
      if (subcommand && subcommand !== "help" && subcommand !== "--help") {
        process.exitCode = 1;
      }
  }
}
