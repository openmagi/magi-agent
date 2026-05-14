/**
 * Example: Weather Lookup Tool
 *
 * Demonstrates a simple network tool that fetches weather data.
 * Uses the `net` permission class since it makes HTTP requests.
 */

import type { Tool, ToolContext, ToolResult } from "../../../src/Tool.js";

interface WeatherLookupInput {
  city: string;
  units?: "metric" | "imperial";
}

interface WeatherLookupOutput {
  city: string;
  temperature: number;
  units: string;
  description: string;
}

export function makeWeatherLookupTool(): Tool<
  WeatherLookupInput,
  WeatherLookupOutput
> {
  return {
    name: "WeatherLookup",
    description:
      "Look up current weather conditions for a city. Returns temperature and description.",
    permission: "net",
    inputSchema: {
      type: "object",
      properties: {
        city: {
          type: "string",
          description: "City name (e.g. 'Seoul', 'New York')",
        },
        units: {
          type: "string",
          enum: ["metric", "imperial"],
          description: "Temperature units (default: metric)",
        },
      },
      required: ["city"],
      additionalProperties: false,
    },

    validate(input: WeatherLookupInput): string | null {
      if (!input.city || input.city.trim().length === 0) {
        return "city must be a non-empty string";
      }
      if (
        input.units &&
        input.units !== "metric" &&
        input.units !== "imperial"
      ) {
        return 'units must be "metric" or "imperial"';
      }
      return null;
    },

    async execute(
      input: WeatherLookupInput,
      _ctx: ToolContext,
    ): Promise<ToolResult<WeatherLookupOutput>> {
      const startMs = Date.now();
      const units = input.units ?? "metric";

      try {
        // Example: uses wttr.in free API (no key needed)
        const url = `https://wttr.in/${encodeURIComponent(input.city)}?format=j1`;
        const res = await fetch(url, {
          signal: AbortSignal.timeout(10_000),
        });

        if (!res.ok) {
          return {
            status: "error",
            errorCode: "api_error",
            errorMessage: `Weather API returned ${res.status}`,
            durationMs: Date.now() - startMs,
          };
        }

        const data = (await res.json()) as Record<string, unknown>;
        const current = (
          data.current_condition as Array<Record<string, unknown>>
        )?.[0];

        if (!current) {
          return {
            status: "error",
            errorCode: "parse_error",
            errorMessage: "Could not parse weather data",
            durationMs: Date.now() - startMs,
          };
        }

        const tempC = Number(current.temp_C);
        const tempF = Number(current.temp_F);
        const desc = (
          current.weatherDesc as Array<Record<string, string>>
        )?.[0]?.value;

        return {
          status: "ok",
          output: {
            city: input.city,
            temperature: units === "metric" ? tempC : tempF,
            units: units === "metric" ? "C" : "F",
            description: desc ?? "Unknown",
          },
          durationMs: Date.now() - startMs,
        };
      } catch (err) {
        return {
          status: "error",
          errorCode: "fetch_error",
          errorMessage: (err as Error).message,
          durationMs: Date.now() - startMs,
        };
      }
    },
  };
}
