import type { AgentEvent } from "../transport/SseWriter.js";

const DIM = "\x1b[2m";
const RESET = "\x1b[0m";
const YELLOW = "\x1b[33m";

export class TerminalSseWriter {
  private ended = false;
  private inThinking = false;

  start(): void {}

  agent(event: AgentEvent): void {
    if (this.ended) return;

    switch (event.type) {
      case "text_delta":
        if (this.inThinking) {
          process.stdout.write(`${RESET}\n`);
          this.inThinking = false;
        }
        process.stdout.write(event.delta);
        break;

      case "thinking_delta":
        if (!this.inThinking) {
          process.stdout.write(`${DIM}`);
          this.inThinking = true;
        }
        process.stdout.write(event.delta);
        break;

      case "tool_start":
        if (this.inThinking) {
          process.stdout.write(`${RESET}\n`);
          this.inThinking = false;
        }
        process.stdout.write(
          `${DIM}[tool] ${event.name}${event.input_preview ? ` ${event.input_preview}` : ""}${RESET}\n`,
        );
        break;

      case "tool_end":
        process.stdout.write(
          `${DIM}[tool] ${event.id} ${event.status} (${event.durationMs}ms)${RESET}\n`,
        );
        break;

      case "error":
        process.stdout.write(
          `\n${YELLOW}Error [${event.code}]: ${event.message}${RESET}\n`,
        );
        break;

      case "turn_end":
        if (this.inThinking) {
          process.stdout.write(`${RESET}`);
          this.inThinking = false;
        }
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
    if (this.inThinking) {
      process.stdout.write(`${RESET}`);
      this.inThinking = false;
    }
    process.stdout.write("\n");
  }
}
