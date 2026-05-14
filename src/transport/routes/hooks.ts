/**
 * Hook management routes — /v1/hooks + /api/hooks.
 *
 * Provides runtime introspection and control over registered hooks:
 * list, detail, enable/disable, remove custom hooks, and NL→config.
 */

import type { RouteHandler } from "./_helpers.js";
import {
  route,
  authorizeGateway,
  authorizeBearer,
  readJsonBody,
  writeJson,
} from "./_helpers.js";
import type { HttpServerCtx } from "./_helpers.js";
import {
  buildHookFromNaturalLanguage,
  type NLHookLLM,
  type GeneratedHookConfig,
} from "../../hooks/NaturalLanguageHookBuilder.js";

function buildLLMFromCtx(ctx: HttpServerCtx): NLHookLLM {
  const config = ctx.agent.config;
  const apiUrl = config.apiProxyUrl;
  const token = config.gatewayToken;

  return {
    async complete(system: string, user: string): Promise<string> {
      const body = {
        model: "claude-haiku-4-5-20251001",
        max_tokens: 2048,
        system,
        messages: [{ role: "user", content: user }],
      };
      const res = await fetch(`${apiUrl}/v1/messages`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-api-key": token,
          "anthropic-version": "2023-06-01",
        },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        throw new Error(`LLM request failed: ${res.status} ${res.statusText}`);
      }
      const json = (await res.json()) as Record<string, unknown>;
      const content = json.content;
      if (!Array.isArray(content) || content.length === 0) {
        throw new Error("Empty LLM response");
      }
      const firstBlock = content[0] as Record<string, unknown>;
      if (typeof firstBlock.text !== "string") {
        throw new Error("Unexpected LLM response format");
      }
      return firstBlock.text;
    },
  };
}

export const hooksRoutes: RouteHandler[] = [
  // GET /v1/hooks — list all hooks with stats
  route("GET", /^\/v1\/hooks(\?|$)/, async (_req, res, _match, ctx) => {
    if (!authorizeGateway(_req, res, ctx)) return;
    const hooks = ctx.agent.hooks.listDetailed();
    writeJson(res, 200, { hooks });
  }),

  // GET /v1/hooks/:name — single hook detail
  route("GET", /^\/v1\/hooks\/([^/?]+)/, async (_req, res, match, ctx) => {
    if (!authorizeGateway(_req, res, ctx)) return;
    const name = decodeURIComponent(match[1] as string);
    const all = ctx.agent.hooks.listDetailed();
    const hook = all.find((h) => h.name === name);
    if (!hook) {
      writeJson(res, 404, { error: "not_found", message: `hook "${name}" not found` });
      return;
    }
    writeJson(res, 200, { hook });
  }),

  // POST /v1/hooks/:name/disable
  route("POST", /^\/v1\/hooks\/([^/?]+)\/disable/, async (_req, res, match, ctx) => {
    if (!authorizeGateway(_req, res, ctx)) return;
    const name = decodeURIComponent(match[1] as string);
    ctx.agent.hooks.disable(name);
    writeJson(res, 200, { ok: true, name, enabled: false });
  }),

  // POST /v1/hooks/:name/enable
  route("POST", /^\/v1\/hooks\/([^/?]+)\/enable/, async (_req, res, match, ctx) => {
    if (!authorizeGateway(_req, res, ctx)) return;
    const name = decodeURIComponent(match[1] as string);
    ctx.agent.hooks.enable(name);
    writeJson(res, 200, { ok: true, name, enabled: true });
  }),

  // DELETE /v1/hooks/:name — remove custom hook (builtin rejected)
  route("DELETE", /^\/v1\/hooks\/([^/?]+)/, async (_req, res, match, ctx) => {
    if (!authorizeGateway(_req, res, ctx)) return;
    const name = decodeURIComponent(match[1] as string);
    const removed = ctx.agent.hooks.unregister(name);
    if (!removed) {
      writeJson(res, 400, { error: "cannot_remove", message: "builtin hooks cannot be removed" });
      return;
    }
    writeJson(res, 200, { ok: true, removed: name });
  }),

  // POST /api/hooks/from-natural-language — NL→config
  route(
    "POST",
    /^\/api\/hooks\/from-natural-language$/,
    async (req, res, _match, ctx) => {
      if (!authorizeBearer(req, res, ctx)) return;

      let body: Record<string, unknown>;
      try {
        body = (await readJsonBody(req)) as Record<string, unknown>;
      } catch (err) {
        writeJson(res, 400, {
          error: "invalid_body",
          message: (err as Error).message,
        });
        return;
      }

      const description = body.description;
      if (typeof description !== "string" || description.trim().length === 0) {
        writeJson(res, 400, {
          error: "missing_field",
          message: "description is required and must be a non-empty string",
        });
        return;
      }

      const langRaw = body.language;
      const language: "ko" | "en" | undefined =
        langRaw === "ko" ? "ko" : langRaw === "en" ? "en" : undefined;
      const rule: { description: string; language?: "ko" | "en" } = {
        description: description.trim(),
        ...(language ? { language } : {}),
      };

      let config: GeneratedHookConfig;
      try {
        const llm = buildLLMFromCtx(ctx);
        config = await buildHookFromNaturalLanguage(rule, llm);
      } catch (err) {
        writeJson(res, 500, {
          error: "generation_failed",
          message: (err as Error).message,
        });
        return;
      }

      writeJson(res, 200, config);
    },
  ),
];
