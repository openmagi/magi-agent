import { PostHog } from "posthog-node";

let _client: PostHog | null = null;

function getPostHogServer(): PostHog | null {
  const key = process.env.NEXT_PUBLIC_POSTHOG_KEY;
  const host = process.env.NEXT_PUBLIC_POSTHOG_HOST || "https://us.i.posthog.com";

  if (!key) return null;

  if (!_client) {
    _client = new PostHog(key, {
      host,
      flushAt: 1, // Flush immediately — optimized for serverless
      flushInterval: 0,
    });
  }

  return _client;
}

export function captureServerEvent(
  userId: string,
  event: string,
  properties?: Record<string, unknown>
): void {
  const client = getPostHogServer();
  if (!client) return;

  client.capture({
    distinctId: userId,
    event,
    properties,
  });
}
