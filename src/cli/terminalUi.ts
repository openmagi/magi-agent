const BOLD = "\x1b[1m";
const DIM = "\x1b[2m";
const RESET = "\x1b[0m";
const GREEN = "\x1b[32m";
const MAGENTA = "\x1b[35m";
const CYAN = "\x1b[36m";

const ANSI_PATTERN = /\x1b\[[0-9;]*m/g;

export function stripAnsi(value: string): string {
  return value.replace(ANSI_PATTERN, "");
}

function visibleLength(value: string): number {
  return stripAnsi(value).length;
}

function truncateMiddle(value: string, maxLength: number): string {
  if (value.length <= maxLength) return value;
  if (maxLength <= 1) return "…";
  const left = Math.ceil((maxLength - 1) / 2);
  const right = Math.floor((maxLength - 1) / 2);
  return `${value.slice(0, left)}…${value.slice(value.length - right)}`;
}

function boxLine(content: string, width: number): string {
  const padding = Math.max(0, width - visibleLength(content));
  return `│ ${content}${" ".repeat(padding)} │`;
}

export interface CliWelcomeOptions {
  agentName: string;
  provider: string;
  model: string;
  workspaceRoot: string;
  sessionKey: string;
}

export function renderCliWelcome(opts: CliWelcomeOptions): string {
  const width = 72;
  const innerWidth = width - 4;
  const workspace = truncateMiddle(opts.workspaceRoot, innerWidth - 11);
  const session = truncateMiddle(opts.sessionKey, innerWidth - 9);
  const model = truncateMiddle(`${opts.provider}/${opts.model}`, innerWidth - 7);
  const title = `${MAGENTA}${BOLD}Magi${RESET} ${DIM}local agent runtime${RESET}`;

  return [
    "",
    `╭─ ${title}${"─".repeat(Math.max(0, width - visibleLength(title) - 3))}╮`,
    boxLine(`${BOLD}Welcome to ${opts.agentName}${RESET}`, innerWidth),
    boxLine(`${DIM}Model:${RESET} ${model}`, innerWidth),
    boxLine(`${DIM}Workspace:${RESET} ${workspace}`, innerWidth),
    boxLine(`${DIM}Session:${RESET} ${session}`, innerWidth),
    boxLine(`${DIM}Try:${RESET} /help, /status, /compact, /reset, /exit`, innerWidth),
    `╰${"─".repeat(width)}╯`,
    "",
  ].join("\n");
}

export function renderCliHelp(): string {
  return [
    "",
    `${BOLD}Magi CLI commands${RESET}`,
    "",
    `  ${CYAN}/help${RESET}      Show this help.`,
    `  ${CYAN}/clear${RESET}     Clear the terminal screen.`,
    `  ${CYAN}/exit${RESET}      Exit interactive chat.`,
    `  ${CYAN}/quit${RESET}      Exit interactive chat.`,
    "",
    `${BOLD}runtime slash commands${RESET}`,
    "",
    "  /status    Show runtime/session status.",
    "  /compact   Compact the current session context.",
    "  /reset     Start a fresh runtime session namespace.",
    "",
    `${DIM}Enter sends. Use your terminal paste mode for multi-line prompts.${RESET}`,
    "",
  ].join("\n");
}

export function renderPrompt(): string {
  return `${GREEN}${BOLD}magi${RESET} ${DIM}›${RESET} `;
}
