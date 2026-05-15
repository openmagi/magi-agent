const ALLOWED_ORIGINS = [
  "https://openmagi.ai",
  "https://www.openmagi.ai",
  "http://localhost:3000",
];

/**
 * Returns a validated origin from the request, falling back to the production URL.
 * Prevents open redirect via Origin header manipulation.
 */
export function getValidOrigin(request: Request): string {
  const origin = request.headers.get("origin");
  if (origin && ALLOWED_ORIGINS.includes(origin)) {
    return origin;
  }
  return "https://openmagi.ai";
}
