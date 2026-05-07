#!/usr/bin/env node
/**
 * magi-agent CLI entrypoint.
 *
 * Commands:
 *   init              Create magi-agent.yaml interactively
 *   start             Interactive terminal mode
 *   serve [--port N]  HTTP API server
 *   version           Print version
 *   (no args)         Show help
 *
 * Zero external CLI dependencies — uses only process.argv for arg parsing.
 */

import fs from "node:fs";
import path from "node:path";
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
  start              Start the agent in interactive terminal mode
  serve [--port N]   Start the HTTP API server (default port: 8080)
  version            Print version

${BOLD}Examples:${RESET}
  ${DIM}$ magi-agent init${RESET}
  ${DIM}$ magi-agent start${RESET}
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

function parsePort(args: string[]): number | undefined {
  const idx = args.indexOf("--port");
  if (idx === -1) return undefined;

  const raw = args[idx + 1];
  if (!raw) {
    console.error("Error: --port requires a number argument.");
    process.exit(1);
  }

  const port = Number.parseInt(raw, 10);
  if (!Number.isFinite(port) || port <= 0 || port >= 65536) {
    console.error(`Error: invalid port "${raw}". Must be 1-65535.`);
    process.exit(1);
  }

  return port;
}

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  const command = args[0];

  switch (command) {
    case "init": {
      const { runInit } = await import("./init.js");
      await runInit();
      break;
    }

    case "start": {
      const { runStart } = await import("./start.js");
      await runStart();
      break;
    }

    case "serve": {
      const port = parsePort(args);
      const { runServe } = await import("./serve.js");
      await runServe(port);
      break;
    }

    case "version":
    case "--version":
    case "-v":
      printVersion();
      break;

    case "help":
    case "--help":
    case "-h":
      printHelp();
      break;

    case undefined:
      printHelp();
      break;

    default:
      console.error(`Unknown command: "${command}"`);
      console.error(`Run "magi-agent --help" for usage.`);
      process.exit(1);
  }
}

main().catch((err) => {
  console.error(`Fatal error: ${(err as Error).message}`);
  process.exit(1);
});
