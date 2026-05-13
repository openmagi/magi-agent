export type CliCommand =
  | { command: "help" }
  | { command: "version" }
  | { command: "init" }
  | { command: "chat" }
  | { command: "serve"; port?: number }
  | {
      command: "run";
      prompt?: string;
      sessionKey?: string;
      model?: string;
      planMode?: boolean;
    }
  | { command: "hook"; args: string[] };

export class CliUsageError extends Error {
  readonly exitCode = 1;
}

function readOptionValue(args: string[], index: number, name: string): string {
  const value = args[index + 1];
  if (!value || value.startsWith("--")) {
    throw new CliUsageError(`Error: ${name} requires a value.`);
  }
  return value;
}

function parsePort(raw: string): number {
  const port = Number.parseInt(raw, 10);
  if (!Number.isFinite(port) || port <= 0 || port >= 65_536) {
    throw new CliUsageError(`Error: invalid port "${raw}". Must be 1-65535.`);
  }
  return port;
}

function parseServeArgs(args: string[]): { command: "serve"; port?: number } {
  let port: number | undefined;
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    if (arg === "--port") {
      port = parsePort(readOptionValue(args, i, "--port"));
      i += 1;
      continue;
    }
    if (arg?.startsWith("--port=")) {
      port = parsePort(arg.slice("--port=".length));
      continue;
    }
    throw new CliUsageError(`Error: unknown serve option "${arg}".`);
  }
  return port === undefined ? { command: "serve" } : { command: "serve", port };
}

function parseRunArgs(args: string[]): Extract<CliCommand, { command: "run" }> {
  let sessionKey: string | undefined;
  let model: string | undefined;
  let planMode = false;
  const promptParts: string[] = [];

  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    if (arg === "--session") {
      sessionKey = readOptionValue(args, i, "--session");
      i += 1;
      continue;
    }
    if (arg?.startsWith("--session=")) {
      sessionKey = arg.slice("--session=".length);
      continue;
    }
    if (arg === "--model") {
      model = readOptionValue(args, i, "--model");
      i += 1;
      continue;
    }
    if (arg?.startsWith("--model=")) {
      model = arg.slice("--model=".length);
      continue;
    }
    if (arg === "--plan") {
      planMode = true;
      continue;
    }
    if (arg?.startsWith("--")) {
      throw new CliUsageError(`Error: unknown run option "${arg}".`);
    }
    if (arg !== undefined) promptParts.push(arg);
  }

  const prompt = promptParts.join(" ").trim();
  return {
    command: "run",
    ...(prompt ? { prompt } : {}),
    ...(sessionKey ? { sessionKey } : {}),
    ...(model ? { model } : {}),
    ...(planMode ? { planMode } : {}),
  };
}

export function parseCliArgs(args: string[]): CliCommand {
  const command = args[0];
  const rest = args.slice(1);

  switch (command) {
    case undefined:
    case "help":
    case "--help":
    case "-h":
      return { command: "help" };
    case "version":
    case "--version":
    case "-v":
      return { command: "version" };
    case "init":
      return { command: "init" };
    case "chat":
    case "start":
      return { command: "chat" };
    case "serve":
      return parseServeArgs(rest);
    case "run":
      return parseRunArgs(rest);
    case "hook":
      return { command: "hook", args: rest };
    default:
      throw new CliUsageError(`Unknown command: "${command}".`);
  }
}
