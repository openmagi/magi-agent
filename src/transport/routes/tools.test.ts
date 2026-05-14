import { describe, expect, it, vi } from "vitest";
import type { IncomingMessage, ServerResponse } from "node:http";
import type { Agent } from "../../Agent.js";
import type { HttpServerCtx } from "./_helpers.js";
import type { ToolMetadata, ToolStats } from "../../tools/ToolRegistry.js";
import { toolsRoutes } from "./tools.js";

function mockReq(
  method: string,
  url: string,
  headers: Record<string, string> = {},
): IncomingMessage {
  return { method, url, headers } as unknown as IncomingMessage;
}

function mockRes(): ServerResponse & { _status: number; _body: string } {
  const res = {
    _status: 0,
    _body: "",
    headersSent: false,
    writeHead(status: number) {
      res._status = status;
      return res;
    },
    end(body?: string) {
      res._body = body ?? "";
    },
  } as unknown as ServerResponse & { _status: number; _body: string };
  return res;
}

const BUILTIN_TOOL: ToolMetadata = {
  name: "FileRead",
  description: "Read a file",
  permission: "read",
  kind: "core",
  enabled: true,
  source: "builtin",
  isConcurrencySafe: true,
  dangerous: false,
  tags: [],
  stats: { calls: 5, errors: 0, avgDurationMs: 10, lastCallAt: 1000 },
};

const EXTERNAL_TOOL: ToolMetadata = {
  name: "my-ext-tool",
  description: "External tool",
  permission: "read",
  kind: "external",
  enabled: true,
  source: "external",
  isConcurrencySafe: false,
  dangerous: false,
  tags: ["test"],
  stats: { calls: 0, errors: 0, avgDurationMs: 0, lastCallAt: 0 },
};

function makeCtx(overrides?: {
  enableResult?: boolean;
  disableResult?: boolean;
  unregisterResult?: boolean;
}): HttpServerCtx {
  const { enableResult = true, disableResult = true, unregisterResult = true } =
    overrides ?? {};
  const statsMap = new Map<string, ToolStats>();
  statsMap.set("FileRead", { ...BUILTIN_TOOL.stats });
  statsMap.set("my-ext-tool", { ...EXTERNAL_TOOL.stats });

  return {
    bearerToken: "gateway-token",
    agent: {
      tools: {
        listAll: () => [{ ...BUILTIN_TOOL }, { ...EXTERNAL_TOOL }],
        list: () => [],
        enable: vi.fn().mockReturnValue(enableResult),
        disable: vi.fn().mockReturnValue(disableResult),
        unregister: vi.fn().mockReturnValue(unregisterResult),
        getToolStats: () => statsMap,
      },
    } as unknown as Agent,
  };
}

function findRoute(
  method: string,
  url: string,
): {
  handler: (typeof toolsRoutes)[number];
  match: RegExpMatchArray | boolean;
} | null {
  for (const handler of toolsRoutes) {
    const req = mockReq(method, url);
    const match = handler.match(req, url);
    if (match !== null && match !== false) {
      return { handler, match };
    }
  }
  return null;
}

describe("tools admin routes", () => {
  it("GET /v1/admin/tools returns list", async () => {
    const found = findRoute("GET", "/v1/admin/tools");
    expect(found).not.toBeNull();
    const res = mockRes();
    const req = mockReq("GET", "/v1/admin/tools", {
      "x-gateway-token": "gateway-token",
    });
    await found!.handler.handle(req, res, found!.match, makeCtx());
    expect(res._status).toBe(200);
    const body = JSON.parse(res._body) as { tools: ToolMetadata[] };
    expect(body.tools).toHaveLength(2);
    expect(body.tools[0].name).toBe("FileRead");
    expect(body.tools[1].name).toBe("my-ext-tool");
  });

  it("GET /v1/admin/tools/:name returns single tool", async () => {
    const found = findRoute("GET", "/v1/admin/tools/FileRead");
    expect(found).not.toBeNull();
    const res = mockRes();
    const req = mockReq("GET", "/v1/admin/tools/FileRead", {
      "x-gateway-token": "gateway-token",
    });
    await found!.handler.handle(req, res, found!.match, makeCtx());
    expect(res._status).toBe(200);
    const body = JSON.parse(res._body) as { tool: ToolMetadata };
    expect(body.tool.name).toBe("FileRead");
  });

  it("GET /v1/admin/tools/:name returns 404 for unknown tool", async () => {
    const found = findRoute("GET", "/v1/admin/tools/unknown");
    expect(found).not.toBeNull();
    const res = mockRes();
    const req = mockReq("GET", "/v1/admin/tools/unknown", {
      "x-gateway-token": "gateway-token",
    });
    await found!.handler.handle(req, res, found!.match, makeCtx());
    expect(res._status).toBe(404);
  });

  it("PUT enable works", async () => {
    const found = findRoute("PUT", "/v1/admin/tools/FileRead/enable");
    expect(found).not.toBeNull();
    const res = mockRes();
    const req = mockReq("PUT", "/v1/admin/tools/FileRead/enable", {
      "x-gateway-token": "gateway-token",
    });
    await found!.handler.handle(req, res, found!.match, makeCtx());
    expect(res._status).toBe(200);
    const body = JSON.parse(res._body) as { ok: boolean; enabled: boolean };
    expect(body.ok).toBe(true);
    expect(body.enabled).toBe(true);
  });

  it("PUT disable works", async () => {
    const found = findRoute("PUT", "/v1/admin/tools/FileRead/disable");
    expect(found).not.toBeNull();
    const res = mockRes();
    const req = mockReq("PUT", "/v1/admin/tools/FileRead/disable", {
      "x-gateway-token": "gateway-token",
    });
    await found!.handler.handle(req, res, found!.match, makeCtx());
    expect(res._status).toBe(200);
    const body = JSON.parse(res._body) as { ok: boolean; enabled: boolean };
    expect(body.ok).toBe(true);
    expect(body.enabled).toBe(false);
  });

  it("PUT enable returns 404 for unknown tool", async () => {
    const found = findRoute("PUT", "/v1/admin/tools/unknown/enable");
    expect(found).not.toBeNull();
    const res = mockRes();
    const req = mockReq("PUT", "/v1/admin/tools/unknown/enable", {
      "x-gateway-token": "gateway-token",
    });
    await found!.handler.handle(
      req,
      res,
      found!.match,
      makeCtx({ enableResult: false }),
    );
    expect(res._status).toBe(404);
  });

  it("DELETE removes external tool", async () => {
    const found = findRoute("DELETE", "/v1/admin/tools/my-ext-tool");
    expect(found).not.toBeNull();
    const res = mockRes();
    const req = mockReq("DELETE", "/v1/admin/tools/my-ext-tool", {
      "x-gateway-token": "gateway-token",
    });
    await found!.handler.handle(req, res, found!.match, makeCtx());
    expect(res._status).toBe(200);
    const body = JSON.parse(res._body) as { ok: boolean; removed: string };
    expect(body.ok).toBe(true);
    expect(body.removed).toBe("my-ext-tool");
  });

  it("DELETE rejects builtin tool", async () => {
    const found = findRoute("DELETE", "/v1/admin/tools/FileRead");
    expect(found).not.toBeNull();
    const res = mockRes();
    const req = mockReq("DELETE", "/v1/admin/tools/FileRead", {
      "x-gateway-token": "gateway-token",
    });
    await found!.handler.handle(
      req,
      res,
      found!.match,
      makeCtx({ unregisterResult: false }),
    );
    expect(res._status).toBe(400);
    const body = JSON.parse(res._body) as { error: string };
    expect(body.error).toBe("cannot_remove");
  });

  it("returns 401 without auth", async () => {
    const found = findRoute("GET", "/v1/admin/tools");
    expect(found).not.toBeNull();
    const res = mockRes();
    const req = mockReq("GET", "/v1/admin/tools");
    await found!.handler.handle(req, res, found!.match, makeCtx());
    expect(res._status).toBe(401);
  });

  it("GET /v1/admin/tools/stats returns stats", async () => {
    const found = findRoute("GET", "/v1/admin/tools/stats");
    expect(found).not.toBeNull();
    const res = mockRes();
    const req = mockReq("GET", "/v1/admin/tools/stats", {
      "x-gateway-token": "gateway-token",
    });
    await found!.handler.handle(req, res, found!.match, makeCtx());
    expect(res._status).toBe(200);
    const body = JSON.parse(res._body) as { stats: Record<string, ToolStats> };
    expect(body.stats).toBeDefined();
    expect(body.stats["FileRead"]).toBeDefined();
    expect(body.stats["FileRead"].calls).toBe(5);
  });
});
