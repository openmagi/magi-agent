/**
 * ToolSearch — on-demand discovery of deferred tools.
 *
 * Deferred tools are sent to the API with `defer_loading: true`, which
 * hides their schema from the model. When the model needs a deferred
 * tool, it calls ToolSearch which returns `tool_reference` content
 * blocks. The API then injects the full schema into the model's context.
 *
 * Inspired by Claude Code's ToolSearchTool.
 */

import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import type { ToolRegistry } from "./ToolRegistry.js";

interface ToolSearchInput {
  query: string;
  max_results?: number;
}

interface ToolReference {
  type: "tool_reference";
  tool_name: string;
}

interface ToolSearchOutput {
  tool_references: ToolReference[];
  query: string;
  total_deferred: number;
}

function parseToolName(name: string): string[] {
  return name
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/[_-]/g, " ")
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
}

export const TOOL_SEARCH_NAME = "ToolSearch";

export function makeToolSearchTool(
  registry: ToolRegistry,
): Tool<ToolSearchInput, ToolSearchOutput> {
  return {
    name: TOOL_SEARCH_NAME,
    description:
      "Fetches full schema definitions for deferred tools so they can be called.\n\n" +
      "Deferred tools appear by name in <system-reminder> messages. Until fetched, " +
      "only the name is known — there is no parameter schema, so the tool cannot be invoked.\n\n" +
      "Query forms:\n" +
      '- "select:Browser,CronCreate" — fetch these exact tools by name\n' +
      '- "browser web" — keyword search, up to max_results best matches\n' +
      '- "+cron create" — require "cron" in the name, rank by remaining terms',
    inputSchema: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description:
            'Query to find deferred tools. Use "select:<name>[,<name>...]" for direct selection, or keywords to search.',
        },
        max_results: {
          type: "number",
          description: "Maximum number of results to return (default: 5)",
        },
      },
      required: ["query"],
    },
    permission: "meta",
    kind: "core",

    execute(
      input: ToolSearchInput,
      _ctx: ToolContext,
    ): Promise<ToolResult<ToolSearchOutput>> {
      const started = Date.now();
      const { query, max_results: maxResults = 5 } = input;

      const allTools = registry.list();
      const deferredTools = allTools.filter((t) => t.shouldDefer === true);

      const selectMatch = query.match(/^select:(.+)$/i);
      if (selectMatch) {
        const requested = selectMatch[1]!
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);
        const refs: ToolReference[] = [];
        for (const name of requested) {
          const found = allTools.find(
            (t) => t.name.toLowerCase() === name.toLowerCase(),
          );
          if (found) {
            refs.push({ type: "tool_reference", tool_name: found.name });
          }
        }
        return Promise.resolve({
          status: "ok",
          output: {
            tool_references: refs,
            query,
            total_deferred: deferredTools.length,
          },
          durationMs: Date.now() - started,
        });
      }

      const queryTerms = query
        .toLowerCase()
        .split(/\s+/)
        .filter((t) => t.length > 0);
      const requiredTerms: string[] = [];
      const optionalTerms: string[] = [];
      for (const term of queryTerms) {
        if (term.startsWith("+") && term.length > 1) {
          requiredTerms.push(term.slice(1));
        } else {
          optionalTerms.push(term);
        }
      }
      const scoringTerms =
        requiredTerms.length > 0
          ? [...requiredTerms, ...optionalTerms]
          : queryTerms;

      const scored = deferredTools
        .map((tool) => {
          const nameParts = parseToolName(tool.name);
          const descLower = tool.description.toLowerCase();

          if (requiredTerms.length > 0) {
            const allMatch = requiredTerms.every(
              (t) =>
                nameParts.some((p) => p.includes(t)) || descLower.includes(t),
            );
            if (!allMatch) return { name: tool.name, score: 0 };
          }

          let score = 0;
          for (const term of scoringTerms) {
            if (nameParts.includes(term)) score += 10;
            else if (nameParts.some((p) => p.includes(term))) score += 5;
            if (descLower.includes(term)) score += 2;
          }
          return { name: tool.name, score };
        })
        .filter((s) => s.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, maxResults);

      return Promise.resolve({
        status: "ok",
        output: {
          tool_references: scored.map((s) => ({
            type: "tool_reference" as const,
            tool_name: s.name,
          })),
          query,
          total_deferred: deferredTools.length,
        },
        durationMs: Date.now() - started,
      });
    },
  };
}
