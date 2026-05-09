import type { AgentEvent } from "../transport/SseWriter.js";

const BOLD = "\x1b[1m";
const DIM = "\x1b[2m";
const RESET = "\x1b[0m";
const GREEN = "\x1b[32m";
const YELLOW = "\x1b[33m";

type TerminalOutput = {
  write(chunk: string): boolean;
};

export interface TerminalSseWriterOptions {
  output?: TerminalOutput;
  showAssistantLabel?: boolean;
  verboseThinking?: boolean;
}

export class TerminalSseWriter {
  private ended = false;
  private inThinking = false;
  private thinkingCollapsed = false;
  private assistantLabelPrinted = false;
  private readonly output: TerminalOutput;
  private readonly showAssistantLabel: boolean;
  private readonly verboseThinking: boolean;
  private readonly toolNames = new Map<string, string>();

  constructor(options: TerminalSseWriterOptions = {}) {
    this.output = options.output ?? process.stdout;
    this.showAssistantLabel = options.showAssistantLabel ?? true;
    this.verboseThinking = options.verboseThinking ?? false;
  }

  start(): void {}

  agent(event: AgentEvent): void {
    if (this.ended) return;

    switch (event.type) {
      case "text_delta":
        this.closeThinkingForVisibleOutput();
        this.printAssistantLabel();
        this.output.write(event.delta);
        break;

      case "thinking_delta":
        if (this.verboseThinking) {
          if (!this.inThinking) {
            this.output.write(`${DIM}∴ Thinking…\n`);
            this.inThinking = true;
          }
          this.output.write(event.delta);
          break;
        }
        if (!this.thinkingCollapsed) {
          this.output.write(`${DIM}∴ Thinking…${RESET}\n`);
          this.thinkingCollapsed = true;
        }
        break;

      case "tool_start":
        this.closeThinkingForVisibleOutput();
        this.toolNames.set(event.id, event.name);
        this.output.write(
          `${DIM}● Running ${event.name}${event.input_preview ? ` ${event.input_preview}` : ""}${RESET}\n`,
        );
        break;

      case "tool_progress":
        this.closeThinkingForVisibleOutput();
        this.output.write(`${DIM}│ ${event.label}${RESET}\n`);
        break;

      case "tool_end":
        this.closeThinkingForVisibleOutput();
        this.output.write(
          `${DIM}└ ${event.status === "ok" ? "Done" : "Finished"} ${this.toolNames.get(event.id) ?? event.id} ${event.status} (${event.durationMs}ms)${event.output_preview ? ` ${event.output_preview}` : ""}${RESET}\n`,
        );
        break;

      case "response_clear":
        this.assistantLabelPrinted = false;
        break;

      case "error":
        this.closeThinkingForVisibleOutput();
        this.output.write(
          `\n${YELLOW}Error [${event.code}]: ${event.message}${RESET}\n`,
        );
        break;

      case "turn_end":
        this.closeThinkingForVisibleOutput();
        break;

      default:
        break;
    }
  }

  legacyDelta(_content: string): void {}

  legacyFinish(): void {}

  end(): void {
    if (this.ended) return;
    this.ended = true;
    this.closeThinkingForVisibleOutput();
    this.output.write("\n");
  }

  private printAssistantLabel(): void {
    if (!this.showAssistantLabel || this.assistantLabelPrinted) return;
    this.output.write(`${GREEN}${BOLD}Magi${RESET}\n`);
    this.assistantLabelPrinted = true;
  }

  private closeThinkingForVisibleOutput(): void {
    if (!this.inThinking) return;
    this.output.write(`${RESET}\n`);
    this.inThinking = false;
  }
}
