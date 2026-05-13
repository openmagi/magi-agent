/**
 * CLI `magi hook` subcommands — create, list, enable, disable, test,
 * logs.
 *
 * Wired into the main CLI router via `src/cli/index.ts`.
 */

import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";

import { parse as parseYaml, stringify as stringifyYaml } from "yaml";
import { loadMagiConfig, magiConfigPath } from "../../config/MagiConfig.js";
import { loadUserHooks } from "../../hooks/HookLoader.js";
import { HookLogger } from "../../hooks/HookLogger.js";
import type { HookPoint } from "../../hooks/types.js";
import {
  buildHookFromNaturalLanguage,
  type NLHookLLM,
  type GeneratedHookConfig,
} from "../../hooks/NaturalLanguageHookBuilder.js";

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
/*  Valid hook points (for CLI validation)                              */
/* ------------------------------------------------------------------ */

const VALID_HOOK_POINTS: ReadonlySet<string> = new Set([
  "beforeTurnStart",
  "afterTurnEnd",
  "beforeLLMCall",
  "afterLLMCall",
  "beforeToolUse",
  "afterToolUse",
  "beforeCommit",
  "afterCommit",
  "onAbort",
  "onError",
  "onTaskCheckpoint",
  "beforeCompaction",
  "afterCompaction",
  "onRuleViolation",
  "onArtifactCreated",
]);

/* ------------------------------------------------------------------ */
/*  `magi hook create <name> --point <hookPoint>`                      */
/* ------------------------------------------------------------------ */

export async function hookCreate(args: string[]): Promise<void> {
  const name = args[0];
  if (!name) {
    console.error('Usage: magi hook create <name> --point <hookPoint>');
    process.exitCode = 1;
    return;
  }

  const pointIdx = args.indexOf("--point");
  const point = pointIdx !== -1 ? args[pointIdx + 1] : undefined;
  if (!point || !VALID_HOOK_POINTS.has(point)) {
    console.error(
      `Error: --point must be one of: ${[...VALID_HOOK_POINTS].join(", ")}`,
    );
    process.exitCode = 1;
    return;
  }

  const hooksDir = path.resolve(process.cwd(), "hooks");
  const fixturesDir = path.join(hooksDir, "__fixtures__", name);
  const hookFile = path.join(hooksDir, `${name}.ts`);

  if (fs.existsSync(hookFile)) {
    console.error(`Error: Hook file already exists: ${hookFile}`);
    process.exitCode = 1;
    return;
  }

  // Scaffold hook file
  const hookContent = `/**
 * ${name} hook — custom ${point} hook.
 *
 * Edit this file to implement your hook logic.
 */

import type {
  HookArgs,
  HookContext,
  HookResult,
  RegisteredHook,
} from "magi-agent/hooks/types";

const hook: RegisteredHook<"${point}"> = {
  name: "${name}",
  point: "${point}",
  priority: 100,
  blocking: true,
  timeoutMs: 5_000,

  async handler(
    args: HookArgs["${point}"],
    ctx: HookContext,
  ): Promise<HookResult<HookArgs["${point}"]> | void> {
    // TODO: Implement your hook logic here
    ctx.log("info", \`${name} hook fired\`, { point: "${point}" });
    return { action: "continue" };
  },
};

export default hook;
`;

  // Scaffold fixture file
  const fixtureContent = `# ${name} — basic test fixture
#
# Run with: magi hook test ${name}

description: "Basic ${name} test case"
point: "${point}"
input:
  ${getDefaultFixtureInput(point as HookPoint)}
expected:
  action: "continue"
`;

  fs.mkdirSync(fixturesDir, { recursive: true });
  fs.writeFileSync(hookFile, hookContent, "utf-8");
  fs.writeFileSync(
    path.join(fixturesDir, "basic.yaml"),
    fixtureContent,
    "utf-8",
  );

  console.log(`${GREEN}Created hook:${RESET} ${hookFile}`);
  console.log(`${GREEN}Created fixture:${RESET} ${fixturesDir}/basic.yaml`);
}

function getDefaultFixtureInput(point: HookPoint): string {
  switch (point) {
    case "beforeCommit":
      return 'assistantText: "Hello world"\n  toolCallCount: 0\n  toolReadHappened: false\n  userMessage: "test"\n  retryCount: 0';
    case "beforeToolUse":
      return 'toolName: "Bash"\n  toolUseId: "tu-1"\n  input: { command: "echo test" }';
    case "beforeLLMCall":
      return 'messages: []\n  tools: []\n  system: "test"\n  iteration: 0';
    case "beforeTurnStart":
      return 'userMessage: "test"';
    default:
      return '# Add input fields for this hook point';
  }
}

/* ------------------------------------------------------------------ */
/*  `magi hook list`                                                   */
/* ------------------------------------------------------------------ */

export async function hookList(): Promise<void> {
  const config = loadMagiConfig();
  const { hooks: userHooks, warnings } = await loadUserHooks({
    directory: config.hooks.directory,
    globalDirectory: config.hooks.global_directory,
  });

  for (const w of warnings) {
    console.warn(`${YELLOW}warn:${RESET} ${w}`);
  }

  // Collect all hooks for display
  interface HookRow {
    name: string;
    point: string;
    priority: number;
    blocking: boolean;
    source: string;
    enabled: boolean;
  }

  const rows: HookRow[] = [];

  // User hooks
  for (const h of userHooks) {
    const override = config.hooks.overrides[h.name];
    rows.push({
      name: h.name,
      point: h.point,
      priority: override?.priority ?? h.priority ?? 100,
      blocking: override?.blocking ?? h.blocking ?? true,
      source: "user",
      enabled: override?.enabled !== false,
    });
  }

  if (rows.length === 0) {
    console.log(`${DIM}No hooks found.${RESET}`);
    console.log(
      `${DIM}Create one with: magi hook create <name> --point <hookPoint>${RESET}`,
    );
    return;
  }

  // Print table
  const nameWidth = Math.max(20, ...rows.map((r) => r.name.length + 2));
  const pointWidth = Math.max(18, ...rows.map((r) => r.point.length + 2));

  console.log(
    `${BOLD}${padRight("NAME", nameWidth)}${padRight("POINT", pointWidth)}${padRight("PRI", 6)}${padRight("BLOCK", 8)}${padRight("SOURCE", 14)}STATUS${RESET}`,
  );
  console.log("-".repeat(nameWidth + pointWidth + 6 + 8 + 14 + 10));

  for (const r of rows) {
    const status = r.enabled
      ? `${GREEN}enabled${RESET}`
      : `${RED}disabled${RESET}`;
    console.log(
      `${padRight(r.name, nameWidth)}${padRight(r.point, pointWidth)}${padRight(String(r.priority), 6)}${padRight(r.blocking ? "yes" : "no", 8)}${padRight(r.source, 14)}${status}`,
    );
  }
}

/* ------------------------------------------------------------------ */
/*  `magi hook enable <name>` / `magi hook disable <name>`             */
/* ------------------------------------------------------------------ */

export async function hookToggle(
  name: string,
  enable: boolean,
): Promise<void> {
  if (!name) {
    console.error(
      `Usage: magi hook ${enable ? "enable" : "disable"} <name>`,
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

  // Ensure hooks.overrides section exists
  if (!doc.hooks || typeof doc.hooks !== "object") {
    doc.hooks = {};
  }
  const hooks = doc.hooks as Record<string, unknown>;
  if (!hooks.overrides || typeof hooks.overrides !== "object") {
    hooks.overrides = {};
  }
  const overrides = hooks.overrides as Record<string, Record<string, unknown>>;

  if (!overrides[name]) {
    overrides[name] = {};
  }
  overrides[name].enabled = enable;

  fs.writeFileSync(configPath, stringifyYaml(doc), "utf-8");

  const verb = enable ? "enabled" : "disabled";
  console.log(
    `${GREEN}Hook "${name}" ${verb}.${RESET} Updated ${configPath}`,
  );
}

/* ------------------------------------------------------------------ */
/*  `magi hook test [name] [--all] [--verbose]`                        */
/* ------------------------------------------------------------------ */

export async function hookTest(args: string[]): Promise<void> {
  const runAll = args.includes("--all");
  const verbose = args.includes("--verbose");
  const nameArg = args.find((a) => !a.startsWith("--"));

  const config = loadMagiConfig();
  const { hooks, warnings } = await loadUserHooks({
    directory: config.hooks.directory,
    globalDirectory: config.hooks.global_directory,
  });

  for (const w of warnings) {
    console.warn(`${YELLOW}warn:${RESET} ${w}`);
  }

  const targets = nameArg
    ? hooks.filter((h) => h.name === nameArg)
    : runAll
      ? hooks
      : [];

  if (targets.length === 0) {
    if (nameArg) {
      console.error(`Hook "${nameArg}" not found.`);
    } else {
      console.error(
        "Usage: magi hook test <name> or magi hook test --all",
      );
    }
    process.exitCode = 1;
    return;
  }

  const fixturesBase = path.resolve(process.cwd(), "hooks", "__fixtures__");
  let passed = 0;
  let failed = 0;

  for (const hook of targets) {
    const fixtureDir = path.join(fixturesBase, hook.name);
    if (!fs.existsSync(fixtureDir)) {
      console.log(
        `${YELLOW}skip${RESET} ${hook.name} — no fixtures at ${fixtureDir}`,
      );
      continue;
    }

    const fixtureFiles = fs
      .readdirSync(fixtureDir)
      .filter((f) => f.endsWith(".yaml") || f.endsWith(".yml"));

    for (const file of fixtureFiles) {
      const fixturePath = path.join(fixtureDir, file);
      try {
        const raw = fs.readFileSync(fixturePath, "utf-8");
        const fixture = parseYaml(raw) as Record<string, unknown>;

        if (verbose) {
          console.log(`${DIM}  fixture: ${file}${RESET}`);
          console.log(`${DIM}  input: ${JSON.stringify(fixture.input)}${RESET}`);
        }

        // For now, just verify the fixture is parseable and the hook
        // handler can be invoked without throwing
        const mockCtx = {
          botId: "test",
          userId: "test",
          sessionKey: "test",
          turnId: "test",
          llm: {} as never,
          transcript: [],
          emit: () => {},
          log: () => {},
          agentModel: "test",
          abortSignal: new AbortController().signal,
          deadlineMs: 5_000,
        };

        const result = await hook.handler(
          fixture.input as never,
          mockCtx,
        );

        const expected = fixture.expected as
          | Record<string, unknown>
          | undefined;
        if (expected?.action && result) {
          const resultAction = (
            result as Record<string, unknown>
          ).action;
          if (resultAction !== expected.action) {
            console.log(
              `${RED}FAIL${RESET} ${hook.name}/${file}: expected action="${expected.action}", got="${String(resultAction)}"`,
            );
            failed++;
            continue;
          }
        }

        console.log(`${GREEN}PASS${RESET} ${hook.name}/${file}`);
        passed++;
      } catch (err) {
        console.log(
          `${RED}FAIL${RESET} ${hook.name}/${file}: ${(err as Error).message}`,
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
/*  `magi hook logs <name> [--since <date>] [--limit <n>]`             */
/* ------------------------------------------------------------------ */

export async function hookLogs(args: string[]): Promise<void> {
  const name = args.find((a) => !a.startsWith("--"));
  if (!name) {
    console.error("Usage: magi hook logs <name> [--since <date>] [--limit <n>]");
    process.exitCode = 1;
    return;
  }

  const sinceIdx = args.indexOf("--since");
  const sinceStr = sinceIdx !== -1 ? args[sinceIdx + 1] : undefined;
  const since = sinceStr ? new Date(sinceStr) : undefined;

  const limitIdx = args.indexOf("--limit");
  const limitStr = limitIdx !== -1 ? args[limitIdx + 1] : undefined;
  const limit = limitStr ? parseInt(limitStr, 10) : undefined;

  const logger = new HookLogger();
  const entries = logger.getLogs(name, { since, limit });

  if (entries.length === 0) {
    console.log(`${DIM}No log entries for "${name}".${RESET}`);
    return;
  }

  for (const entry of entries) {
    const ts = entry.timestamp;
    const dur = `${entry.durationMs}ms`;
    const err = entry.error ? ` ${RED}error=${entry.error}${RESET}` : "";
    const reason = entry.reason ? ` reason="${entry.reason}"` : "";
    console.log(
      `${DIM}${ts}${RESET} ${entry.point} action=${entry.action} ${dur}${reason}${err}`,
    );
  }
}

/* ------------------------------------------------------------------ */
/*  `magi hook create-from-rule "<description>"`                       */
/* ------------------------------------------------------------------ */

/**
 * Build a minimal LLM client for the create-from-rule command.
 * Uses the configured LLM provider from magi-agent.yaml or env vars.
 */
async function buildCliLLM(): Promise<NLHookLLM> {
  const { loadConfig } = await import("../config.js");
  const { buildCliAgentConfig, cleanToken } = await import("../agentConfig.js");
  const config = loadConfig();
  const agentConfig = buildCliAgentConfig(config, { botId: "cli-hook-builder" });

  const apiUrl = agentConfig.apiProxyUrl;
  const token = cleanToken(agentConfig.gatewayToken) ?? "";
  const model = "claude-haiku-4-5-20251001";

  return {
    async complete(system: string, user: string): Promise<string> {
      const body = {
        model,
        max_tokens: 2048,
        system,
        messages: [{ role: "user", content: user }],
      };
      const res = await fetch(`${apiUrl}/v1/messages`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-api-key": token,
          "anthropic-version": "2023-06-01",
        },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        throw new Error(`LLM request failed: ${res.status} ${res.statusText}`);
      }
      const json = (await res.json()) as Record<string, unknown>;
      const content = json.content;
      if (!Array.isArray(content) || content.length === 0) {
        throw new Error("Empty LLM response");
      }
      const firstBlock = content[0] as Record<string, unknown>;
      if (typeof firstBlock.text !== "string") {
        throw new Error("Unexpected LLM response format");
      }
      return firstBlock.text;
    },
  };
}

function askConfirm(question: string): Promise<boolean> {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });
  return new Promise((resolve) => {
    rl.question(`${question} [y/N] `, (answer) => {
      rl.close();
      resolve(answer.toLowerCase() === "y" || answer.toLowerCase() === "yes");
    });
  });
}

function writeGeneratedHook(config: GeneratedHookConfig): void {
  const hooksDir = path.resolve(process.cwd(), "hooks");
  const fixturesDir = path.join(hooksDir, "__fixtures__", config.name);
  const hookFile = path.join(hooksDir, `${config.name}.ts`);

  fs.mkdirSync(fixturesDir, { recursive: true });
  fs.writeFileSync(hookFile, config.hookCode, "utf-8");
  fs.writeFileSync(
    path.join(fixturesDir, "basic.yaml"),
    config.fixtureYaml,
    "utf-8",
  );

  console.log(`${GREEN}Created hook:${RESET} ${hookFile}`);
  console.log(`${GREEN}Created fixture:${RESET} ${fixturesDir}/basic.yaml`);

  // Update magi.config.yaml if classifier dimension was generated
  if (config.classifierDimension) {
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

    // Ensure classifier.custom_dimensions exists
    if (!doc.classifier || typeof doc.classifier !== "object") {
      doc.classifier = {};
    }
    const classifier = doc.classifier as Record<string, unknown>;
    if (
      !classifier.custom_dimensions ||
      typeof classifier.custom_dimensions !== "object"
    ) {
      classifier.custom_dimensions = {};
    }
    const dims = classifier.custom_dimensions as Record<
      string,
      Record<string, unknown>
    >;

    dims[config.classifierDimension.name] = {
      phase: config.classifierDimension.phase,
      prompt: config.classifierDimension.prompt,
      output_schema: config.classifierDimension.output_schema,
    };

    fs.writeFileSync(configPath, stringifyYaml(doc), "utf-8");
    console.log(
      `${GREEN}Updated classifier dimension:${RESET} ${configPath}`,
    );
  }
}

export async function hookCreateFromRule(args: string[]): Promise<void> {
  const description = args.filter((a) => !a.startsWith("--")).join(" ").trim();
  const noConfirm = args.includes("--yes") || args.includes("-y");

  if (!description) {
    console.error(
      'Usage: magi hook create-from-rule "<natural language rule>"',
    );
    console.error("");
    console.error("Examples:");
    console.error(
      '  magi hook create-from-rule "Block responses containing drug dosage outside safe ranges"',
    );
    console.error(
      '  magi hook create-from-rule "투자 조언이 포함된 응답에 면책조항 경고 추가"',
    );
    process.exitCode = 1;
    return;
  }

  console.log(`${DIM}Parsing rule...${RESET}`);

  let llm: NLHookLLM;
  try {
    llm = await buildCliLLM();
  } catch (err) {
    console.error(
      `Failed to initialize LLM client: ${(err as Error).message}`,
    );
    console.error(
      `${DIM}Make sure magi-agent.yaml is configured with valid LLM credentials.${RESET}`,
    );
    process.exitCode = 1;
    return;
  }

  let config: GeneratedHookConfig;
  try {
    config = await buildHookFromNaturalLanguage({ description }, llm);
  } catch (err) {
    console.error(
      `Failed to generate hook config: ${(err as Error).message}`,
    );
    process.exitCode = 1;
    return;
  }

  // Show preview
  console.log("");
  console.log(`${BOLD}Generated Hook Configuration${RESET}`);
  console.log(`${"─".repeat(50)}`);
  console.log(`${BOLD}Name:${RESET}     ${config.name}`);
  console.log(`${BOLD}Point:${RESET}    ${config.point}`);
  console.log(`${BOLD}Priority:${RESET} ${config.priority}`);
  console.log(`${BOLD}Blocking:${RESET} ${config.blocking}`);
  if (config.classifierDimension) {
    console.log(
      `${BOLD}Classifier:${RESET} ${config.classifierDimension.name} (${config.classifierDimension.phase})`,
    );
  }
  console.log(`${"─".repeat(50)}`);
  console.log("");
  console.log(`${BOLD}magi.config.yaml snippet:${RESET}`);
  console.log(`${DIM}${config.yamlConfig}${RESET}`);

  if (noConfirm) {
    writeGeneratedHook(config);
    return;
  }

  const confirmed = await askConfirm("Write hook files?");
  if (!confirmed) {
    console.log(`${DIM}Aborted.${RESET}`);
    return;
  }

  writeGeneratedHook(config);
}

/* ------------------------------------------------------------------ */
/*  Router                                                             */
/* ------------------------------------------------------------------ */

export async function runHookCommand(args: string[]): Promise<void> {
  const subcommand = args[0];
  const rest = args.slice(1);

  switch (subcommand) {
    case "create":
      await hookCreate(rest);
      break;
    case "create-from-rule":
      await hookCreateFromRule(rest);
      break;
    case "list":
      await hookList();
      break;
    case "enable":
      await hookToggle(rest[0] ?? "", true);
      break;
    case "disable":
      await hookToggle(rest[0] ?? "", false);
      break;
    case "test":
      await hookTest(rest);
      break;
    case "logs":
      await hookLogs(rest);
      break;
    default:
      console.log(`
${BOLD}magi hook${RESET} — manage hooks

${BOLD}Subcommands:${RESET}
  create <name> --point <hookPoint>   Scaffold a new hook + fixture
  create-from-rule "<description>"    Generate hook from natural language rule
  list                                List all registered hooks
  enable <name>                       Enable a hook in magi.config.yaml
  disable <name>                      Disable a hook in magi.config.yaml
  test [name] [--all] [--verbose]     Run hook fixtures
  logs <name> [--since <date>] [--limit <n>]  View hook execution logs
`);
      if (subcommand && subcommand !== "help" && subcommand !== "--help") {
        process.exitCode = 1;
      }
  }
}
