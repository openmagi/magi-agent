/**
 * Example: Database Query Tool
 *
 * Demonstrates a dangerous tool pattern. Marked as `dangerous: true`
 * so the runtime requires explicit user consent before execution.
 * Uses the `net` permission class since it connects to external databases.
 */

import type { Tool, ToolContext, ToolResult } from "../../../src/Tool.js";

interface DatabaseQueryInput {
  query: string;
  connectionString?: string;
}

interface DatabaseQueryOutput {
  rows: unknown[];
  rowCount: number;
}

export function makeDatabaseQueryTool(): Tool<
  DatabaseQueryInput,
  DatabaseQueryOutput
> {
  return {
    name: "DatabaseQuery",
    description:
      "Execute a read-only SQL query against a PostgreSQL database. " +
      "Dangerous: requires explicit user approval before execution.",
    permission: "net",
    dangerous: true,
    mutatesWorkspace: false,
    inputSchema: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "SQL query to execute (read-only; DDL/DML blocked)",
        },
        connectionString: {
          type: "string",
          description:
            "PostgreSQL connection string (default: DATABASE_URL env var)",
        },
      },
      required: ["query"],
      additionalProperties: false,
    },

    validate(input: DatabaseQueryInput): string | null {
      if (!input.query || input.query.trim().length === 0) {
        return "query must be a non-empty string";
      }
      // Block dangerous SQL statements
      const upper = input.query.toUpperCase().trim();
      const blocked = [
        "DROP",
        "DELETE",
        "TRUNCATE",
        "ALTER",
        "CREATE",
        "INSERT",
        "UPDATE",
        "GRANT",
        "REVOKE",
      ];
      for (const keyword of blocked) {
        if (upper.startsWith(keyword)) {
          return `Blocked: ${keyword} statements are not allowed. Only SELECT queries are permitted.`;
        }
      }
      return null;
    },

    async execute(
      input: DatabaseQueryInput,
      _ctx: ToolContext,
    ): Promise<ToolResult<DatabaseQueryOutput>> {
      const startMs = Date.now();

      // In a real implementation, you would use a PostgreSQL client library.
      // This example shows the pattern without a real DB dependency.
      const connectionString =
        input.connectionString ?? process.env.DATABASE_URL;

      if (!connectionString) {
        return {
          status: "error",
          errorCode: "no_connection",
          errorMessage:
            "No connection string provided and DATABASE_URL is not set",
          durationMs: Date.now() - startMs,
        };
      }

      // Placeholder: real implementation would execute the query here
      return {
        status: "error",
        errorCode: "not_implemented",
        errorMessage:
          "This is an example tool. Install a PostgreSQL client (e.g. pg) " +
          "and implement the query execution logic.",
        durationMs: Date.now() - startMs,
      };
    },
  };
}
