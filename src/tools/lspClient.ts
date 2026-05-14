import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";

interface JsonRpcRequest {
  jsonrpc: "2.0";
  id: number;
  method: string;
  params?: unknown;
}

interface JsonRpcNotification {
  jsonrpc: "2.0";
  method: string;
  params?: unknown;
}

interface JsonRpcResponse {
  jsonrpc: "2.0";
  id: number;
  result?: unknown;
  error?: {
    code?: number;
    message?: string;
    data?: unknown;
  };
}

interface PendingRequest {
  method: string;
  resolve: (value: unknown) => void;
  reject: (err: Error) => void;
  timer: NodeJS.Timeout;
}

export interface LspClientOptions {
  command: string;
  args?: string[];
  cwd: string;
  timeoutMs?: number;
  onNotification?: (method: string, params: unknown) => void;
}

export class StdioLspClient {
  private readonly timeoutMs: number;
  private readonly onNotification?: (method: string, params: unknown) => void;
  private readonly proc: ChildProcessWithoutNullStreams;
  private readonly pending = new Map<number, PendingRequest>();
  private nextId = 1;
  private buffer = Buffer.alloc(0);
  private stderr = "";
  private closed = false;

  constructor(options: LspClientOptions) {
    this.timeoutMs = options.timeoutMs ?? 5_000;
    this.onNotification = options.onNotification;
    this.proc = spawn(options.command, options.args ?? [], {
      cwd: options.cwd,
      env: process.env,
      stdio: "pipe",
    });
    this.proc.stdout.on("data", (chunk: Buffer) => this.readStdout(chunk));
    this.proc.stderr.on("data", (chunk: Buffer) => {
      this.stderr = `${this.stderr}${chunk.toString("utf8")}`.slice(-4_000);
    });
    this.proc.on("error", (err) => this.failAll(err));
    this.proc.on("exit", (code, signal) => {
      this.closed = true;
      if (this.pending.size > 0) {
        const detail = this.stderr.trim();
        this.failAll(
          new Error(
            `Language server exited before responding (code=${code ?? "null"}, signal=${signal ?? "null"}${detail ? `, stderr=${detail}` : ""})`,
          ),
        );
      }
    });
  }

  async initialize(rootUri: string): Promise<unknown> {
    const result = await this.request("initialize", {
      processId: process.pid,
      rootUri,
      capabilities: {
        textDocument: {
          codeAction: { dynamicRegistration: false },
          definition: { dynamicRegistration: false },
          documentSymbol: { dynamicRegistration: false },
          hover: { dynamicRegistration: false },
          references: { dynamicRegistration: false },
          rename: { dynamicRegistration: false },
          synchronization: { didSave: true },
          publishDiagnostics: { relatedInformation: false },
        },
        workspace: {
          symbol: { dynamicRegistration: false },
          workspaceEdit: { documentChanges: true },
        },
      },
    });
    this.notify("initialized", {});
    return result;
  }

  openTextDocument(uri: string, languageId: string, version: number, text: string): void {
    this.notify("textDocument/didOpen", {
      textDocument: {
        uri,
        languageId,
        version,
        text,
      },
    });
  }

  async request(method: string, params?: unknown): Promise<unknown> {
    if (this.closed) {
      throw new Error("Language server is closed");
    }
    const id = this.nextId++;
    const payload: JsonRpcRequest = { jsonrpc: "2.0", id, method, params };
    const promise = new Promise<unknown>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Language server request timed out: ${method}`));
      }, this.timeoutMs);
      this.pending.set(id, { method, resolve, reject, timer });
    });
    this.write(payload);
    return promise;
  }

  notify(method: string, params?: unknown): void {
    if (this.closed) return;
    this.write({ jsonrpc: "2.0", method, params });
  }

  async shutdown(): Promise<void> {
    if (this.closed) return;
    try {
      await this.request("shutdown", null);
      this.notify("exit");
    } finally {
      this.dispose();
    }
  }

  dispose(): void {
    if (this.closed) return;
    this.closed = true;
    for (const [id, pending] of this.pending) {
      clearTimeout(pending.timer);
      pending.reject(new Error(`Language server request cancelled: ${pending.method}`));
      this.pending.delete(id);
    }
    this.proc.kill("SIGTERM");
  }

  private write(payload: JsonRpcRequest | JsonRpcNotification): void {
    const body = JSON.stringify(payload);
    this.proc.stdin.write(`Content-Length: ${Buffer.byteLength(body, "utf8")}\r\n\r\n${body}`);
  }

  private readStdout(chunk: Buffer): void {
    this.buffer = Buffer.concat([this.buffer, chunk]);
    while (true) {
      const headerEnd = this.buffer.indexOf("\r\n\r\n");
      if (headerEnd < 0) return;
      const header = this.buffer.slice(0, headerEnd).toString("utf8");
      const lengthMatch = /^Content-Length: (\d+)$/im.exec(header);
      if (!lengthMatch) {
        this.failAll(new Error("Language server response missing Content-Length header"));
        return;
      }
      const length = Number(lengthMatch[1]);
      const bodyStart = headerEnd + 4;
      if (this.buffer.length < bodyStart + length) return;
      const body = this.buffer.slice(bodyStart, bodyStart + length).toString("utf8");
      this.buffer = this.buffer.slice(bodyStart + length);
      this.handleMessage(JSON.parse(body) as JsonRpcResponse | JsonRpcNotification);
    }
  }

  private handleMessage(message: JsonRpcResponse | JsonRpcNotification): void {
    if ("id" in message && typeof message.id === "number") {
      const pending = this.pending.get(message.id);
      if (!pending) return;
      clearTimeout(pending.timer);
      this.pending.delete(message.id);
      if (message.error) {
        pending.reject(
          new Error(
            `Language server request failed: ${pending.method}: ${message.error.message ?? "unknown error"}`,
          ),
        );
      } else {
        pending.resolve(message.result);
      }
      return;
    }

    if ("method" in message) {
      this.onNotification?.(message.method, message.params);
    }
  }

  private failAll(err: Error): void {
    for (const [id, pending] of this.pending) {
      clearTimeout(pending.timer);
      pending.reject(err);
      this.pending.delete(id);
    }
  }
}

export function parseCommandLine(commandLine: string): { command: string; args: string[] } {
  const tokens: string[] = [];
  let token = "";
  let quote: "'" | '"' | null = null;
  let escaping = false;

  for (const ch of commandLine.trim()) {
    if (escaping) {
      token += ch;
      escaping = false;
      continue;
    }
    if (ch === "\\" && quote !== "'") {
      escaping = true;
      continue;
    }
    if ((ch === "'" || ch === '"') && quote === null) {
      quote = ch;
      continue;
    }
    if (ch === quote) {
      quote = null;
      continue;
    }
    if (quote === null && /\s/.test(ch)) {
      if (token.length > 0) {
        tokens.push(token);
        token = "";
      }
      continue;
    }
    token += ch;
  }

  if (escaping || quote !== null) {
    throw new Error("Invalid language server command line");
  }
  if (token.length > 0) tokens.push(token);
  const command = tokens[0];
  if (!command) throw new Error("Language server command is empty");
  return { command, args: tokens.slice(1) };
}
