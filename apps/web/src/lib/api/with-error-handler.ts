import { NextResponse } from "next/server";
import * as Sentry from "@sentry/nextjs";
import { AppError } from "@/lib/errors";

type RouteHandler = (
  request: Request,
  context: { params: Promise<Record<string, string>> }
) => Promise<Response>;

/**
 * Wraps a route handler with standardised error handling.
 * - AppError (4xx) → structured JSON response (not sent to Sentry)
 * - AppError (5xx) → structured JSON response + Sentry capture
 * - Unknown errors → 500 with generic message + Sentry capture
 */
export function withErrorHandler(handler: RouteHandler): RouteHandler {
  return async (request, context) => {
    try {
      return await handler(request, context);
    } catch (error) {
      if (error instanceof AppError) {
        if (error.statusCode >= 500) {
          Sentry.captureException(error);
        }
        return NextResponse.json(
          error.code
            ? { error: error.message, code: error.code }
            : { error: error.message },
          { status: error.statusCode }
        );
      }
      console.error(`[API] Unhandled error on ${request.method} ${new URL(request.url).pathname}:`, error);
      Sentry.captureException(error);
      return NextResponse.json(
        { error: "Internal server error" },
        { status: 500 }
      );
    }
  };
}
