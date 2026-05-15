import { NextResponse } from "next/server";

export class AppError extends Error {
  constructor(
    message: string,
    public statusCode: number = 500,
    public code?: string
  ) {
    super(message);
    this.name = "AppError";
  }
}

// ── Response helpers (used directly in route handlers) ───────────────────────

export function apiError(message: string, status: number = 500, code?: string): NextResponse {
  return NextResponse.json(
    code ? { error: message, code } : { error: message },
    { status }
  );
}

export function unauthorized(): NextResponse {
  return apiError("Unauthorized", 401);
}

export function forbidden(): NextResponse {
  return apiError("Forbidden", 403);
}

export function notFound(resource?: string): NextResponse {
  return apiError(`${resource ?? "Resource"} not found`, 404);
}

export function badRequest(message: string): NextResponse {
  return apiError(message, 400);
}
