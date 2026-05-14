/**
 * HTTP routes for custom tool management.
 *
 * | Method | Path                              | Function                     |
 * |--------|-----------------------------------|------------------------------|
 * | GET    | /api/tools                        | List all tools (config)      |
 * | POST   | /api/tools/:name/enable           | Enable tool (config)         |
 * | POST   | /api/tools/:name/disable          | Disable tool (config)        |
 * | GET    | /api/tools/:name/logs             | Get tool exec logs           |
 * | GET    | /v1/admin/tools                   | All tools with metadata      |
 * | GET    | /v1/admin/tools/stats             | All tool stats               |
 * | GET    | /v1/admin/tools/:name             | Single tool detail           |
 * | PUT    | /v1/admin/tools/:name/enable      | Enable tool (registry)       |
 * | PUT    | /v1/admin/tools/:name/disable     | Disable tool (registry)      |
 * | DELETE | /v1/admin/tools/:name             | Remove external/skill tool   |
 */

import fs from "node:fs";

import { parse as parseYaml, stringify as stringifyYaml } from "yaml";
import type { RouteHandler } from "./_helpers.js";
import {
  route,
  authorizeBearer,
  authorizeGateway,
  writeJson,
  parseUrl,
} from "./_helpers.js";
import {
  loadMagiConfig,
  magiConfigPath,
  resetMagiConfig,
} from "../../config/MagiConfig.js";
import { ToolLogger } from "../../tools/ToolLogger.js";
import type { ToolRegistry } from "../../tools/ToolRegistry.js";

export const toolsRoutes: RouteHandler[] = [
  /* ================================================================== */
  /*  /api/tools routes — MagiConfig-based (OSS CLI/SDK)                 */
  /* ================================================================== */

  /* GET /api/tools — list all tools with source/status */
  route("GET", /^\/api\/tools$/, async (req, res, _match, ctx) => {
    if (!authorizeBearer(req, res, ctx)) return;

    const registry = ctx.agent.tools;
    const config = loadMagiConfig();

    const tools = registry.list().map((t: { name: string; description: string; permission: string; kind?: string; dangerous?: boolean; tags?: string[] }) => {
      const override = config.tools.overrides[t.name];
      return {
        name: t.name,
        description: t.description,
        permission: override?.permission ?? t.permission,
        kind: t.kind ?? "core",
        dangerous: t.dangerous ?? false,
        enabled: override?.enabled !== false,
        tags: t.tags ?? [],
      };
    });

    writeJson(res, 200, { tools });
  }),

  /* POST /api/tools/:name/enable */
  route(
    "POST",
    /^\/api\/tools\/([^/]+)\/enable$/,
    async (req, res, match, ctx) => {
      if (!authorizeBearer(req, res, ctx)) return;

      const name = decodeURIComponent(match[1] ?? "");
      updateToolOverride(name, { enabled: true });
      writeJson(res, 200, { name, enabled: true });
    },
  ),

  /* POST /api/tools/:name/disable */
  route(
    "POST",
    /^\/api\/tools\/([^/]+)\/disable$/,
    async (req, res, match, ctx) => {
      if (!authorizeBearer(req, res, ctx)) return;

      const name = decodeURIComponent(match[1] ?? "");
      updateToolOverride(name, { enabled: false });
      writeJson(res, 200, { name, enabled: false });
    },
  ),

  /* GET /api/tools/:name/logs */
  route(
    "GET",
    /^\/api\/tools\/([^/]+)\/logs/,
    async (req, res, match, ctx) => {
      if (!authorizeBearer(req, res, ctx)) return;

      const name = decodeURIComponent(match[1] ?? "");
      const url = parseUrl(req.url);
      const sinceStr = url.searchParams.get("since");
      const limitStr = url.searchParams.get("limit");

      const since = sinceStr ? new Date(sinceStr) : undefined;
      const limit = limitStr ? parseInt(limitStr, 10) : undefined;

      const logger = new ToolLogger();
      const entries = logger.getLogs(name, { since, limit });

      writeJson(res, 200, { entries });
    },
  ),

  /* ================================================================== */
  /*  /v1/admin/tools routes — ToolRegistry-based (hosted/admin)         */
  /* ================================================================== */

  // GET /v1/admin/tools — all tools with metadata + stats
  route("GET", /^\/v1\/admin\/tools(\?|$)/, async (req, res, _match, ctx) => {
    if (!authorizeGateway(req, res, ctx)) return;
    const tools = (ctx.agent.tools as ToolRegistry).listAll();
    writeJson(res, 200, { tools });
  }),

  // GET /v1/admin/tools/stats — all tool stats (must be before /:name)
  route("GET", /^\/v1\/admin\/tools\/stats(\?|$)/, async (req, res, _match, ctx) => {
    if (!authorizeGateway(req, res, ctx)) return;
    const statsMap = (ctx.agent.tools as ToolRegistry).getToolStats();
    const stats: Record<string, unknown> = {};
    for (const [name, s] of statsMap) {
      stats[name] = s;
    }
    writeJson(res, 200, { stats });
  }),

  // GET /v1/admin/tools/:name — single tool detail
  route("GET", /^\/v1\/admin\/tools\/([^/?]+)(\?|$)/, async (req, res, match, ctx) => {
    if (!authorizeGateway(req, res, ctx)) return;
    const name = decodeURIComponent(match[1] ?? "");
    const all = (ctx.agent.tools as ToolRegistry).listAll();
    const tool = all.find((t) => t.name === name);
    if (!tool) {
      writeJson(res, 404, { error: "not_found", message: `tool "${name}" not found` });
      return;
    }
    writeJson(res, 200, { tool });
  }),

  // PUT /v1/admin/tools/:name/enable
  route("PUT", /^\/v1\/admin\/tools\/([^/?]+)\/enable$/, async (req, res, match, ctx) => {
    if (!authorizeGateway(req, res, ctx)) return;
    const name = decodeURIComponent(match[1] ?? "");
    const ok = (ctx.agent.tools as ToolRegistry).enable(name);
    if (!ok) {
      writeJson(res, 404, { error: "not_found" });
      return;
    }
    writeJson(res, 200, { ok: true, name, enabled: true });
  }),

  // PUT /v1/admin/tools/:name/disable
  route("PUT", /^\/v1\/admin\/tools\/([^/?]+)\/disable$/, async (req, res, match, ctx) => {
    if (!authorizeGateway(req, res, ctx)) return;
    const name = decodeURIComponent(match[1] ?? "");
    const ok = (ctx.agent.tools as ToolRegistry).disable(name);
    if (!ok) {
      writeJson(res, 404, { error: "not_found" });
      return;
    }
    writeJson(res, 200, { ok: true, name, enabled: false });
  }),

  // DELETE /v1/admin/tools/:name — remove external/skill tool
  route("DELETE", /^\/v1\/admin\/tools\/([^/?]+)$/, async (req, res, match, ctx) => {
    if (!authorizeGateway(req, res, ctx)) return;
    const name = decodeURIComponent(match[1] ?? "");
    const removed = (ctx.agent.tools as ToolRegistry).unregister(name);
    if (!removed) {
      writeJson(res, 400, {
        error: "cannot_remove",
        message: "builtin tools cannot be removed",
      });
      return;
    }
    writeJson(res, 200, { ok: true, removed: name });
  }),
];

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function updateToolOverride(
  name: string,
  fields: Record<string, unknown>,
): void {
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

  if (!doc.tools || typeof doc.tools !== "object") {
    doc.tools = {};
  }
  const tools = doc.tools as Record<string, unknown>;
  if (!tools.overrides || typeof tools.overrides !== "object") {
    tools.overrides = {};
  }
  const overrides = tools.overrides as Record<
    string,
    Record<string, unknown>
  >;

  if (!overrides[name]) {
    overrides[name] = {};
  }
  Object.assign(overrides[name], fields);

  fs.writeFileSync(configPath, stringifyYaml(doc), "utf-8");
  resetMagiConfig(); // bust cache so next read sees update
}
