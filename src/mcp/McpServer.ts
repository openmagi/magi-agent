/**
 * McpServer — JSON-RPC 2.0 surface implementing the Model Context
 * Protocol `tools/*` methods (§8.1).
 *
 * Minimal spec-aligned subset:
 *   - `initialize` returns capability handshake
 *   - `tools/list` returns the bot's tool catalogue (optionally
 *     filtered by plan-mode)
 *   - `tools/call` invokes a named tool with input JSON and returns
 *     the stringified tool result content
 *
 * Streaming / notifications / resources are deliberately out of scope;
 * this is a tools-only gateway for external MCP clients (Claude
 * Desktop, Cursor, etc.) to reach a bot's Tool surface over HTTP.
 *
 * Wire transport is handled by `transport/routes/mcp.ts` which passes
 * the parsed request body into `handle()` and writes the returned
 * response (or array of responses for batches).
 */

import type { Agent } from "../Agent.js";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { decideRuntimePermission } from "../permissions/PermissionArbiter.js";
import { PLAN_MODE_ALLOWED_TOOLS } from "../turn/ToolSelector.js";

// ---------------------------------------------------------------------------
// JSON-RPC 2.0 types
// ---------------------------------------------------------------------------

export type JsonRpcId = string | number | null;

export interface JsonRpcRequest {
  jsonrpc: "2.0";
  id?: JsonRpcId;
  method: string;
  params?: unknown;
}

export interface JsonRpcSuccess {
  jsonrpc: "2.0";
  id: JsonRpcId;
  result: unknown;
}

export interface JsonRpcError {
  jsonrpc: "2.0";
  id: JsonRpcId;
  error: {
    code: number;
    message: string;
    data?: unknown;
  };
}

export type JsonRpcResponse = JsonRpcSuccess | JsonRpcError;

// ---------------------------------------------------------------------------
// JSON-RPC error codes (standard + MCP-adjacent)
// ---------------------------------------------------------------------------

export const JSON_RPC_PARSE_ERROR = -32700;
export const JSON_RPC_INVALID_REQUEST = -32600;
export const JSON_RPC_METHOD_NOT_FOUND = -32601;
export const JSON_RPC_INVALID_PARAMS = -32602;
export const JSON_RPC_INTERNAL_ERROR = -32603;

// ---------------------------------------------------------------------------
// Options passed per-request by the HTTP route
// ---------------------------------------------------------------------------

export type McpPermissionMode = "default" | "plan";

export interface McpHandleOptions {
  /** "plan" when `X-MCP-Permission-Mode: plan`; defaults to "default". */
  permissionMode?: McpPermissionMode;
  /** Opaque client identifier (e.g. X-MCP-Client-Id). Surfaced in
   *  audit events and the `initialize` sessionId echo. Optional. */
  clientId?: string;
}

// ---------------------------------------------------------------------------
// McpServer
// ---------------------------------------------------------------------------

export interface McpServerOptions {
  readonly agent: Agent;
  /** Override for tests — default builds a throwaway AbortController. */
  newAbortSignal?: () => AbortSignal;
}

/** MCP tool-entry shape returned by tools/list. */
interface McpToolEntry {
  name: string;
  description: string;
  inputSchema: object;
}

export class McpServer {
  private readonly agent: Agent;
  private readonly newAbortSignal: () => AbortSignal;

  constructor(opts: McpServerOptions) {
    this.agent = opts.agent;
    this.newAbortSignal =
      opts.newAbortSignal ?? (() => new AbortController().signal);
  }

  /**
   * Dispatch a single JSON-RPC request to the matching MCP method.
   * Unknown methods return -32601. Malformed requests (missing method)
   * return -32600. Thrown errors are mapped to -32603.
   */
  async handle(
    req: JsonRpcRequest,
    opts: McpHandleOptions = {},
  ): Promise<JsonRpcResponse> {
    const id: JsonRpcId = req.id ?? null;

    if (req.jsonrpc !== "2.0" || typeof req.method !== "string") {
      return errorResponse(id, JSON_RPC_INVALID_REQUEST, "Invalid Request");
    }

    try {
      switch (req.method) {
        case "initialize":
          return successResponse(id, this.handleInitialize(opts));
        case "tools/list":
          return successResponse(id, {
            tools: this.handleToolsList(opts),
          });
        case "tools/call":
          return await this.handleToolsCall(id, req.params, opts);
        default:
          return errorResponse(
            id,
            JSON_RPC_METHOD_NOT_FOUND,
            `Method not found: ${req.method}`,
          );
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return errorResponse(id, JSON_RPC_INTERNAL_ERROR, msg);
    }
  }

  /** Initialize capability handshake — minimal tools-only profile. */
  private handleInitialize(opts: McpHandleOptions): unknown {
    return {
      protocolVersion: "2025-03-26",
      capabilities: {
        tools: { listChanged: false },
      },
      serverInfo: {
        name: "magi-core-agent",
        version: "0.1.0",
      },
      ...(opts.clientId ? { clientId: opts.clientId } : {}),
    };
  }

  private handleToolsList(opts: McpHandleOptions): McpToolEntry[] {
    const tools = this.filteredTools(opts);
    return tools.map((t) => ({
      name: t.name,
      description: t.description,
      inputSchema: t.inputSchema,
    }));
  }

  private async handleToolsCall(
    id: JsonRpcId,
    rawParams: unknown,
    opts: McpHandleOptions,
  ): Promise<JsonRpcResponse> {
    if (!rawParams || typeof rawParams !== "object") {
      return errorResponse(
        id,
        JSON_RPC_INVALID_PARAMS,
        "Invalid params: object required",
      );
    }
    const params = rawParams as { name?: unknown; arguments?: unknown };
    if (typeof params.name !== "string" || params.name.length === 0) {
      return errorResponse(
        id,
        JSON_RPC_INVALID_PARAMS,
        "Invalid params: `name` is required",
      );
    }
    const argsInput =
      params.arguments === undefined ? {} : params.arguments;
    if (
      argsInput !== null &&
      typeof argsInput !== "object"
    ) {
      return errorResponse(
        id,
        JSON_RPC_INVALID_PARAMS,
        "Invalid params: `arguments` must be an object",
      );
    }

    // Plan-mode filter applies at call site too — clients cannot call
    // a tool that tools/list would have hidden.
    const available = this.filteredTools(opts);
    const tool = available.find((t) => t.name === params.name) ?? null;
    if (!tool) {
      return errorResponse(
        id,
        JSON_RPC_METHOD_NOT_FOUND,
        `Tool not found: ${params.name}`,
      );
    }

    // Per-tool validate() hook — optional deterministic pre-check.
    if (tool.validate) {
      const err = tool.validate(argsInput as never);
      if (err) {
        return errorResponse(
          id,
          JSON_RPC_INVALID_PARAMS,
          `Invalid arguments: ${err}`,
        );
      }
    }

    const permission = await decideRuntimePermission({
      mode: opts.permissionMode === "plan" ? "plan" : "default",
      source: "mcp",
      toolName: tool.name,
      input: argsInput,
      tool,
      workspaceRoot: this.agent.config.workspaceRoot,
    });
    if (permission.decision === "deny") {
      return errorResponse(
        id,
        JSON_RPC_INTERNAL_ERROR,
        `Permission denied: ${permission.reason}`,
        {
          status: "permission_denied",
          securityCritical: permission.securityCritical,
        },
      );
    }
    if (permission.decision === "ask") {
      return errorResponse(
        id,
        JSON_RPC_INTERNAL_ERROR,
        `Permission required: ${permission.reason}`,
        { status: "permission_required" },
      );
    }

    const ctx = this.buildToolContext(tool.name, opts);

    let result: ToolResult;
    try {
      result = await (tool as Tool<unknown, unknown>).execute(
        argsInput,
        ctx,
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return errorResponse(id, JSON_RPC_INTERNAL_ERROR, msg);
    }

    if (result.status !== "ok" && result.status !== "empty") {
      const msg =
        result.errorMessage ??
        result.errorCode ??
        `tool ${tool.name} returned status=${result.status}`;
      return errorResponse(id, JSON_RPC_INTERNAL_ERROR, msg, {
        status: result.status,
        errorCode: result.errorCode,
      });
    }

    const text = stringifyToolOutput(result.output);
    return successResponse(id, {
      content: [
        {
          type: "text",
          text,
        },
      ],
      isError: false,
    });
  }

  /**
   * Build a minimal ToolContext for an out-of-turn tool invocation. No
   * session is attached; the MCP caller is treated as an external
   * client and the call is atomic (no staging / transcript append).
   */
  private buildToolContext(
    toolName: string,
    opts: McpHandleOptions,
  ): ToolContext {
    const clientTag = opts.clientId ? opts.clientId : "anonymous";
    return {
      botId: this.agent.config.botId,
      sessionKey: `mcp:${clientTag}`,
      turnId: `mcp-${Date.now()}-${Math.floor(Math.random() * 1e6).toString(36)}`,
      workspaceRoot: this.agent.config.workspaceRoot,
      abortSignal: this.newAbortSignal(),
      emitProgress: () => {
        /* MCP has no progress channel in this MVP */
      },
      askUser: async () => {
        // MCP has no human-in-the-loop channel; AskUserQuestion is
        // intentionally not callable via this gateway.
        throw new Error(
          `askUser is not available over MCP (tool=${toolName})`,
        );
      },
      staging: {
        stageFileWrite: () => {
          /* no staging — tools write directly like in dispatcher */
        },
        stageTranscriptAppend: () => {
          /* no transcript for MCP calls */
        },
        stageAuditEvent: () => {
          /* audit is handled by the HTTP route layer */
        },
      },
    };
  }

  /** Tools visible given the caller's permission mode. */
  private filteredTools(opts: McpHandleOptions): Tool[] {
    const all = this.agent.tools.list();
    if (opts.permissionMode === "plan") {
      return all.filter((t) => PLAN_MODE_ALLOWED_TOOLS.has(t.name));
    }
    return all;
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function successResponse(
  id: JsonRpcId,
  result: unknown,
): JsonRpcSuccess {
  return { jsonrpc: "2.0", id, result };
}

export function errorResponse(
  id: JsonRpcId,
  code: number,
  message: string,
  data?: unknown,
): JsonRpcError {
  return {
    jsonrpc: "2.0",
    id,
    error: data !== undefined ? { code, message, data } : { code, message },
  };
}

function stringifyToolOutput(output: unknown): string {
  if (output === undefined || output === null) return "";
  if (typeof output === "string") return output;
  try {
    return JSON.stringify(output);
  } catch {
    return String(output);
  }
}
