/**
 * HTTP route: POST /api/hooks/from-natural-language
 *
 * Accepts a natural language rule description and returns a generated
 * hook configuration. Used by the dashboard frontend for the
 * "create hook from plain language" feature.
 */

import type { RouteHandler } from "./_helpers.js";
import {
  route,
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
