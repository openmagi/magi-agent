/**
 * Health routes — /health (lean) + /healthz (rich: tools, skills, hooks).
 *
 * Both endpoints are unauthenticated on purpose: /healthz is what
 * health-monitor pings every 15s; adding auth just to fail-open would
 * be lossy.
 */

import { route, writeJson, type RouteHandler } from "./_helpers.js";
import { transportReliabilityStatus } from "../../reliability/TransportReliability.js";
import { permissionArbiterStatus } from "../../permissions/PermissionArbiter.js";
import type { Agent } from "../../Agent.js";

export interface BuildInfo {
  version: string;
  buildSha: string | null;
  imageRepo: string | null;
  imageTag: string | null;
  imageDigest: string | null;
  builtImage: ImageIdentity;
  expectedImage: ImageIdentity;
}

export interface ImageIdentity {
  buildSha: string | null;
  imageRepo: string | null;
  imageTag: string | null;
  imageDigest: string | null;
}

export interface RuntimeFeatureStatus {
  controlLedger: boolean;
  controlRequest: boolean;
  permissionArbiter: boolean;
  planApproval: boolean;
  structuredOutput: boolean;
  childHarness: boolean;
  transportReliability: boolean;
}

export interface HealthPayload {
  ok: boolean;
  degradedReasons: string[];
  botId: string;
  runtime: "core-agent";
  version: string;
  buildSha: string | null;
  imageRepo: string | null;
  imageTag: string | null;
  imageDigest: string | null;
  builtImage: ImageIdentity;
  expectedImage: ImageIdentity;
  features: RuntimeFeatureStatus;
  rollback: {
    transportReliability: "MAGI_TRANSPORT_RELIABILITY=off";
    safetyCriticalRuntime: "image_rollback_only";
  };
  tools: Array<{ name: string; permission: string }>;
  skills: {
    loaded: number;
    issues: unknown[];
    runtimeHooks: number;
    executableRuntimeHooks: number;
  } | null;
  hooks: Array<{
    name: string;
    point: string;
    priority: number;
    blocking: boolean;
  }>;
  hipocampus: unknown;
  policy: unknown;
  permission_arbiter: ReturnType<typeof permissionArbiterStatus>;
  transport_reliability: ReturnType<typeof transportReliabilityStatus>;
  debug_workflow: unknown;
}

export function readBuildInfo(): BuildInfo {
  const builtImage: ImageIdentity = {
    buildSha: readNonEmptyEnv("MAGI_BUILT_BUILD_SHA"),
    imageRepo: readNonEmptyEnv("MAGI_BUILT_IMAGE_REPO"),
    imageTag: readNonEmptyEnv("MAGI_BUILT_IMAGE_TAG"),
    imageDigest: readNonEmptyEnv("MAGI_BUILT_IMAGE_DIGEST"),
  };
  const expectedImage: ImageIdentity = {
    buildSha:
      readNonEmptyEnv("MAGI_BUILD_SHA") ??
      readNonEmptyEnv("MAGI_BUILD_SHA") ??
      readNonEmptyEnv("VERCEL_GIT_COMMIT_SHA") ??
      null,
    imageRepo: readNonEmptyEnv("MAGI_IMAGE_REPO"),
    imageTag: readNonEmptyEnv("MAGI_IMAGE_TAG"),
    imageDigest:
      readNonEmptyEnv("MAGI_EXPECTED_IMAGE_DIGEST") ??
      readNonEmptyEnv("MAGI_IMAGE_DIGEST") ??
      null,
  };
  return {
    version: readNonEmptyEnv("MAGI_VERSION") ?? "0.19.10",
    buildSha: builtImage.buildSha ?? expectedImage.buildSha,
    imageRepo: builtImage.imageRepo ?? expectedImage.imageRepo,
    imageTag: builtImage.imageTag ?? expectedImage.imageTag,
    imageDigest: builtImage.imageDigest ?? expectedImage.imageDigest,
    builtImage,
    expectedImage,
  };
}

function readNonEmptyEnv(name: string): string | null {
  const value = process.env[name]?.trim();
  return value ? value : null;
}

export function runtimeFeatures(): RuntimeFeatureStatus {
  return {
    controlLedger: true,
    controlRequest: true,
    permissionArbiter: true,
    planApproval: true,
    structuredOutput: true,
    childHarness: true,
    transportReliability: process.env.MAGI_TRANSPORT_RELIABILITY !== "off",
  };
}

export async function buildHealthPayload(input: {
  botId: string;
  tools: Array<{ name: string; permission: string }>;
  hooks: Array<{ name: string; point: string; priority: number; blocking: boolean }>;
  skillReport: { loaded: unknown[]; issues: unknown[]; runtimeHooks: unknown[] } | null;
  hipocampus: unknown;
  policyStatus: () => Promise<unknown>;
  debugWorkflowStatus: () => unknown;
  transportStatus: () => ReturnType<typeof transportReliabilityStatus>;
  buildInfo: BuildInfo;
  features: RuntimeFeatureStatus;
}): Promise<HealthPayload> {
  const degradedReasons: string[] = [];
  let policy: unknown = null;
  let debugWorkflow: unknown = null;

  try {
    policy = await input.policyStatus();
    if (policy == null) {
      degradedReasons.push("policy_status_null");
    }
  } catch {
    degradedReasons.push("policy_status_unavailable");
  }

  try {
    debugWorkflow = input.debugWorkflowStatus();
    if (debugWorkflow == null) {
      degradedReasons.push("debug_workflow_status_null");
    }
  } catch {
    degradedReasons.push("debug_workflow_status_unavailable");
  }

  const transport = input.transportStatus();
  if (transport.enabled && !transport.helperExists) {
    degradedReasons.push("transport_helper_missing");
  }
  if (transport.enabled && !transport.helperWired) {
    degradedReasons.push("transport_helper_unwired");
  }

  return {
    ok: degradedReasons.length === 0,
    degradedReasons,
    botId: input.botId,
    runtime: "core-agent",
    ...input.buildInfo,
    features: input.features,
    rollback: {
      transportReliability: "MAGI_TRANSPORT_RELIABILITY=off",
      safetyCriticalRuntime: "image_rollback_only",
    },
    tools: input.tools,
    skills: input.skillReport
      ? {
          loaded: input.skillReport.loaded.length,
          issues: input.skillReport.issues,
          runtimeHooks: input.skillReport.runtimeHooks.length,
          executableRuntimeHooks: input.skillReport.runtimeHooks.filter(
            (hook) =>
              !!hook &&
              typeof hook === "object" &&
              (hook as { action?: unknown }).action === "command",
          ).length,
        }
      : null,
    hooks: input.hooks,
    hipocampus: input.hipocampus,
    policy,
    permission_arbiter: permissionArbiterStatus(),
    transport_reliability: transport,
    debug_workflow: debugWorkflow,
  };
}

export async function buildHealthPayloadForAgent(agent: Agent): Promise<HealthPayload> {
  const tools = agent.tools.list();
  const skillReport = agent.tools.skillReport();
  const hipocampus = agent.hipocampus ? await agent.hipocampus.status() : null;
  return await buildHealthPayload({
    botId: agent.config.botId,
    tools: tools.map((t) => ({ name: t.name, permission: t.permission })),
    hooks: agent.hooks.list().map((h) => ({
      name: h.name,
      point: h.point,
      priority: h.priority ?? 100,
      blocking: h.blocking !== false,
    })),
    hipocampus,
    skillReport,
    policyStatus: async () =>
      typeof agent.policy?.status === "function"
        ? await agent.policy.status()
        : null,
    debugWorkflowStatus: () =>
      typeof agent.debugWorkflow?.status === "function"
        ? agent.debugWorkflow.status()
        : null,
    transportStatus: () => transportReliabilityStatus(),
    buildInfo: readBuildInfo(),
    features: runtimeFeatures(),
  });
}

export const healthRoutes: RouteHandler[] = [
  route("GET", /^\/health(?:\?.*)?$/, async (_req, res, _m, ctx) => {
    const buildInfo = readBuildInfo();
    writeJson(res, 200, {
      ok: true,
      botId: ctx.agent.config.botId,
      runtime: "core-agent",
      version: buildInfo.version,
      buildSha: buildInfo.buildSha,
    });
  }),
  route("GET", /^\/healthz(?:\?.*)?$/, async (_req, res, _m, ctx) => {
    const payload = await buildHealthPayloadForAgent(ctx.agent);
    writeJson(res, payload.ok ? 200 : 503, payload);
  }),
];
