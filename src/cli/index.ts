#!/usr/bin/env node
/**
 * magi-agent CLI entrypoint.
 *
 * Commands:
 *   init              Create magi-agent.yaml interactively
 *   chat              Interactive terminal mode
 *   start             Backwards-compatible alias for chat
 *   run [prompt]      One-shot terminal mode
 *   serve [--port N]  HTTP API server
 *   version           Print version
 *   (no args)         Show help
 *
 * Zero external CLI dependencies — uses only process.argv for arg parsing.
 */

import fs from "node:fs";
import path from "node:path";
import { CliUsageError, parseCliArgs } from "./args.js";
const BOLD = "\x1b[1m";
const DIM = "\x1b[2m";
const RESET = "\x1b[0m";

function printHelp(): void {
  console.log(`
${BOLD}magi-agent${RESET} — Autonomous task runtime with agentic interaction

${BOLD}Usage:${RESET}
  magi-agent <command> [options]

${BOLD}Commands:${RESET}
  init               Create a magi-agent.yaml config file interactively
  chat               Start the agent in interactive terminal mode
  start              Alias for chat
  run [prompt]       Run one prompt and print the streamed result
  hook <subcommand>  Manage hooks (create, list, enable, disable, test, logs)
  tool <subcommand>  Manage custom tools (create, list, enable, disable, test, logs)
  serve [--port N]   Start the HTTP API server (default port: 8080)
  version            Print version

${BOLD}Examples:${RESET}
  ${DIM}$ magi-agent init${RESET}
  ${DIM}$ magi-agent chat${RESET}
  ${DIM}$ magi-agent run "summarize workspace/knowledge"${RESET}
  ${DIM}$ cat notes.md | magi-agent run --session notes${RESET}
  ${DIM}$ magi-agent serve --port 3000${RESET}

${DIM}https://github.com/openmagi/magi-agent${RESET}
`);
}

function printVersion(): void {
  try {
    // Resolve package.json relative to this file. Works whether running
    // from src/ (tsx) or dist/ (compiled).
    const thisDir = __dirname;
    // Walk up until we find package.json
    let dir = thisDir;
    for (let i = 0; i < 5; i++) {
      const candidate = path.join(dir, "package.json");
      if (fs.existsSync(candidate)) {
        const pkg = JSON.parse(fs.readFileSync(candidate, "utf-8"));
        console.log(`magi-agent v${pkg.version ?? "0.0.0"}`);
        return;
      }
      dir = path.dirname(dir);
    }
    console.log("magi-agent (version unknown)");
  } catch {
    console.log("magi-agent (version unknown)");
  }
}

async function main(): Promise<void> {
  const parsed = parseCliArgs(process.argv.slice(2));

  switch (parsed.command) {
    case "init": {
      const { runInit } = await import("./init.js");
      await runInit();
      break;
    }

    case "chat": {
      const { runStart } = await import("./start.js");
      await runStart();
      break;
    }

    case "run": {
      const { runOneShot } = await import("./run.js");
      await runOneShot(parsed);
      break;
    }

    case "serve": {
      const { runServe } = await import("./serve.js");
      await runServe(parsed.port);
      break;
    }

    case "hook": {
      const { runHookCommand } = await import("./commands/hook.js");
      await runHookCommand(parsed.args);
      break;
    }

    case "tool": {
      const { runToolCommand } = await import("./commands/tool.js");
      await runToolCommand(parsed.args);
      break;
    }

    case "version":
      printVersion();
      break;

    case "help":
      printHelp();
      break;
  }
}

main().catch((err) => {
  if (err instanceof CliUsageError) {
    console.error(err.message);
    console.error(`Run "magi-agent --help" for usage.`);
    process.exit(err.exitCode);
  }
  console.error(`Fatal error: ${(err as Error).message}`);
  process.exit(1);
});
