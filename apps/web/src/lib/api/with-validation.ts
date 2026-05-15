import { NextResponse } from "next/server";
import type { ZodSchema, ZodError } from "zod";

type ParseResult<T> =
  | { data: T; error: null }
  | { data: null; error: NextResponse };

/**
 * Parse and validate a request JSON body against a Zod schema.
 * Returns `{ data, error }` — check `error` first to narrow `data` to `T`.
 */
export async function parseBody<T>(
  request: Request,
  schema: ZodSchema<T>
): Promise<ParseResult<T>> {
  let raw: unknown;
  try {
    raw = await request.json();
  } catch {
    return {
      data: null,
      error: NextResponse.json(
        { error: "Invalid JSON", code: "validation_error" },
        { status: 400 }
      ),
    };
  }

  const result = schema.safeParse(raw);
  if (!result.success) {
    return {
      data: null,
      error: NextResponse.json(
        {
          error: "Validation failed",
          code: "validation_error",
          details: formatZodError(result.error),
        },
        { status: 400 }
      ),
    };
  }

  return { data: result.data, error: null };
}

function formatZodError(error: ZodError): Array<{ path: string; message: string }> {
  return error.issues.map((issue) => ({
    path: issue.path.join("."),
    message: issue.message,
  }));
}
