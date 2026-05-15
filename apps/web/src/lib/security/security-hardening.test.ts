import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const root = process.cwd();

function read(path: string): string {
  return readFileSync(join(root, path), "utf8");
}

describe("security hardening coverage", () => {
  it("hardens server-only Supabase RPCs and enables RLS on service-owned tables", () => {
    const migration = read("supabase/migrations/20260510000000_security_harden_remaining_rpcs_and_rls.sql");

    for (const table of [
      "learned_skills",
      "skill_refinement_suggestions",
      "notion_sync_sources",
      "notion_sync_runs",
      "stripe_webhook_events",
    ]) {
      expect(migration).toContain(`ALTER TABLE public.${table} ENABLE ROW LEVEL SECURITY`);
      expect(migration).toContain(`CREATE POLICY "${table}_service_role_all"`);
    }

    for (const fn of [
      "check_and_use_email",
      "reset_email_quota",
      "log_email_usage",
      "check_and_use_search",
      "reset_search_quota",
      "x402_balance",
      "wallet_log_usage",
      "increment_collection_counts",
      "increment_kb_storage",
      "upsert_skill_daily",
      "update_learned_skill_usage",
      "promote_learned_skill_candidates",
      "seed_default_channels",
      "increment_blog_post_view",
    ]) {
      expect(migration).toContain(fn);
    }

    expect(migration).toMatch(/REVOKE EXECUTE ON FUNCTION[\s\S]+FROM anon, authenticated/i);
    expect(migration).toMatch(/GRANT EXECUTE ON FUNCTION[\s\S]+TO service_role/i);
  });

  it("keeps internal worker tokens required on both caller and worker deployments", () => {
    const apiProxy = read("infra/k8s/api-proxy/deployment.yaml");
    const insaneWorker = read("infra/k8s/insane-fetch-worker/deployment.yaml");
    const naverWorker = read("infra/k8s/naver-realestate-worker/deployment.yaml");

    expect(apiProxy).toContain("name: INSANE_FETCH_WORKER_TOKEN");
    expect(apiProxy).toContain("key: insane-fetch-worker-token");
    expect(apiProxy).toContain("name: NAVER_RE_WORKER_TOKEN");
    expect(apiProxy).toContain("key: naver-realestate-worker-token");

    for (const manifest of [insaneWorker, naverWorker]) {
      const tokenBlock = manifest.match(/name: (?:INSANE_FETCH_WORKER_TOKEN|NAVER_RE_WORKER_TOKEN)[\s\S]*?key: [^\n]+(?:\n\s+[^\n]+){0,3}/)?.[0] ?? "";
      expect(tokenBlock).not.toContain("optional: true");
    }
  });

  it("protects provisioning-worker skills downloads with auth and ingress policy", () => {
    const worker = read("infra/docker/provisioning-worker/worker.js");
    const provisioning = read("infra/docker/provisioning-worker/provisioning.js");
    const policy = read("infra/k8s/provisioning-worker/networkpolicy.yaml");

    expect(worker).toContain("validateSkillsGatewayToken");
    expect(worker).toContain("Authorization");
    expect(worker).toContain("x-gateway-token");
    expect(provisioning).toMatch(/Authorization: Bearer \$GATEWAY_TOKEN/);
    expect(policy).toMatch(/policyTypes:[\s\S]*- Ingress[\s\S]*- Egress/);
    expect(policy).toMatch(/clawy-bot: "true"[\s\S]*port: 8080/);
    expect(policy).toMatch(/app: chat-proxy[\s\S]*port: 8080/);
  });

  it("keeps OAuth authorization bearer-only and response headers fail closed in production", () => {
    const chatProxy = read("infra/docker/chat-proxy/chat-proxy.js");
    const nextConfig = read("next.config.ts");
    const validationSchemas = read("src/lib/validation/schemas.ts");
    const telegramConnect = read("src/app/api/bots/[botId]/connect-telegram/route.ts");

    const authorizeBlock = chatProxy.match(/async function handleOAuthAuthorize[\s\S]*?const userId = claims\.sub;/)?.[0] ?? "";
    expect(authorizeBlock).not.toContain('searchParams.get("token")');
    expect(chatProxy).toContain("TELEGRAM_AUTH_ACTIONS");
    expect(chatProxy).toContain("Account status unavailable");
    expect(validationSchemas).toContain("telegramBotTokenSchema");
    expect(telegramConnect).toContain("encodeURIComponent(token)");
    expect(nextConfig).toContain("Strict-Transport-Security");
    expect(nextConfig).toContain("ALLOW_TYPESCRIPT_BUILD_ERRORS");
    expect(nextConfig).toMatch(/isDevelopment \? .*'unsafe-eval'.*: null/);
  });
});
