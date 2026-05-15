export function getAppOrigin(): string {
  const configuredUrl = process.env.NEXT_PUBLIC_APP_URL || "https://openmagi.ai";

  try {
    return new URL(configuredUrl).origin;
  } catch {
    return "https://openmagi.ai";
  }
}
