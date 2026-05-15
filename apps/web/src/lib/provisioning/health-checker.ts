import type { K8sClient } from "./k8s-client";

export interface HealthCheckResult {
  healthy: boolean;
  status: string;
  details?: string;
}

export interface HealthCheckOptions {
  retries?: number;
  delayMs?: number;
}

const DEFAULT_RETRIES = 3;
const DEFAULT_DELAY_MS = 10_000;

// After pod is Running, wait for node-host to connect to gateway
const GATEWAY_READY_RETRIES = 36;
const GATEWAY_READY_DELAY_MS = 5_000;

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Checks the health of a bot's pod in Kubernetes.
 *
 * - Queries pod status via the k8s client
 * - Retries up to `retries` times with `delayMs` between attempts for non-terminal states
 * - Detects provider cooldown by inspecting pod logs for "provider disabled"
 * - Waits for node-host to successfully connect to gateway (no "connect failed" in recent logs)
 * - Verifies gateway container is ready to process Telegram messages
 */
export async function checkBotHealth(
  k8sClient: Pick<K8sClient, "getPodStatus" | "getPodLogs" | "areContainersReady">,
  namespace: string,
  podName: string,
  options?: HealthCheckOptions
): Promise<HealthCheckResult> {
  const retries = options?.retries ?? DEFAULT_RETRIES;
  const delayMs = options?.delayMs ?? DEFAULT_DELAY_MS;

  let lastStatus = "Unknown";

  for (let attempt = 0; attempt < retries; attempt++) {
    try {
      lastStatus = await k8sClient.getPodStatus(namespace, podName);
    } catch (error) {
      lastStatus = "Error";
      if (attempt < retries - 1) {
        await delay(delayMs);
        continue;
      }
      return {
        healthy: false,
        status: lastStatus,
        details: `Failed to get pod status: ${error instanceof Error ? error.message : String(error)}`,
      };
    }

    // Terminal failure states
    if (lastStatus === "Failed" || lastStatus === "Unknown") {
      return {
        healthy: false,
        status: lastStatus,
        details: `Pod is in ${lastStatus} state`,
      };
    }

    // Pod is running -- check logs for provider cooldown
    if (lastStatus === "Running") {
      try {
        const logs = await k8sClient.getPodLogs(namespace, podName);
        if (logs && /provider disabled/i.test(logs)) {
          return {
            healthy: false,
            status: "Running",
            details: "Provider cooldown detected -- provider disabled",
          };
        }
      } catch {
        // Log retrieval failure is non-fatal; pod is still running
      }

      // Pod Running — now wait for node-host to connect to gateway
      const gatewayReady = await waitForGatewayReady(
        k8sClient,
        namespace,
        podName,
      );

      if (!gatewayReady) {
        return {
          healthy: false,
          status: "Running",
          details: "Gateway did not become ready — node-host cannot connect",
        };
      }

      // Verify gateway container is processing Telegram messages
      const gatewayLive = await waitForGatewayLive(
        k8sClient,
        namespace,
        podName,
      );

      if (!gatewayLive) {
        return {
          healthy: false,
          status: "Running",
          details: "Gateway connected but not yet processing Telegram messages",
        };
      }

      return {
        healthy: true,
        status: "Running",
      };
    }

    // For Pending or other transient states, retry
    if (attempt < retries - 1) {
      await delay(delayMs);
    }
  }

  // Exhausted retries
  return {
    healthy: false,
    status: lastStatus,
    details: `Pod did not become ready after ${retries} attempts (last status: ${lastStatus})`,
  };
}

/**
 * Waits for node-host container to successfully connect to gateway.
 * Checks node-host logs — if the latest logs don't contain "connect failed",
 * the connection is established.
 */
async function waitForGatewayReady(
  k8sClient: Pick<K8sClient, "getPodLogs">,
  namespace: string,
  podName: string,
): Promise<boolean> {
  for (let attempt = 0; attempt < GATEWAY_READY_RETRIES; attempt++) {
    try {
      const logs = await k8sClient.getPodLogs(namespace, podName, "node-host");

      // node-host started and no connection failure = gateway is reachable
      if (logs && logs.includes("node host PATH:") && !logs.includes("connect failed")) {
        return true;
      }
    } catch {
      // Log retrieval may fail early — retry
    }

    await delay(GATEWAY_READY_DELAY_MS);
  }

  return false;
}

// After node-host connects, wait for gateway to actually be live on Telegram
const GATEWAY_LIVE_RETRIES = 12;
const GATEWAY_LIVE_DELAY_MS = 5_000;

/**
 * Verifies gateway container is actually processing Telegram messages.
 * Checks gateway logs for Telegram polling/webhook readiness AND verifies
 * all containers report ready via K8s readiness probes.
 *
 * Gateway log patterns indicating readiness:
 * - "telegram" + "polling" or "webhook" — Telegram transport active
 * - "listening" or "started" — gateway HTTP server ready
 */
async function waitForGatewayLive(
  k8sClient: Pick<K8sClient, "getPodLogs" | "areContainersReady">,
  namespace: string,
  podName: string,
): Promise<boolean> {
  for (let attempt = 0; attempt < GATEWAY_LIVE_RETRIES; attempt++) {
    try {
      // Check if all containers are ready (K8s readiness probes)
      const allReady = await k8sClient.areContainersReady(namespace, podName);

      // Check gateway logs for Telegram connectivity
      const logs = await k8sClient.getPodLogs(namespace, podName, "gateway");
      const hasStarted = logs && (
        logs.includes("listening") ||
        logs.includes("started") ||
        logs.includes("gateway run")
      );
      const hasTelegram = logs && (
        logs.includes("telegram") ||
        logs.includes("polling") ||
        logs.includes("webhook")
      );

      if (allReady && hasStarted && hasTelegram) {
        return true;
      }

      // Fallback: if all containers are ready and gateway shows started
      // (Telegram patterns may vary across versions)
      if (allReady && hasStarted && attempt >= GATEWAY_LIVE_RETRIES / 2) {
        return true;
      }
    } catch {
      // Retry on transient errors
    }

    await delay(GATEWAY_LIVE_DELAY_MS);
  }

  // Final fallback: check containers ready one last time
  try {
    return await k8sClient.areContainersReady(namespace, podName);
  } catch {
    return false;
  }
}
