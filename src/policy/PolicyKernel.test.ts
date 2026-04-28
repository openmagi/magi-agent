import { afterEach, describe, expect, it } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { Workspace } from "../storage/Workspace.js";
import { PolicyKernel } from "./PolicyKernel.js";

const roots: string[] = [];

async function makeWorkspaceRoot(userRules?: string): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "policy-kernel-"));
  roots.push(root);
  if (userRules !== undefined) {
    await fs.writeFile(path.join(root, "USER-RULES.md"), userRules, "utf8");
  }
  return root;
}

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => fs.rm(root, { recursive: true, force: true })),
  );
});

describe("PolicyKernel", () => {
  it("returns platform defaults even when user rules are absent", async () => {
    const root = await makeWorkspaceRoot();
    const kernel = new PolicyKernel(new Workspace(root));

    const snapshot = await kernel.current();

    expect(snapshot.policy.approval.explicitConsentForExternalActions).toBe(true);
    expect(snapshot.policy.verification.requireCompletionEvidence).toBe(true);
    expect(snapshot.policy.delivery.requireDeliveredArtifactsBeforeCompletion).toBe(true);
    expect(snapshot.policy.async.requireRealNotificationMechanism).toBe(true);
    expect(snapshot.policy.retry.retryTransientToolFailures).toBe(true);
    expect(snapshot.status.executableDirectives).toContain(
      "approval.explicit_consent_for_external_actions=true",
    );
    expect(snapshot.status.advisoryDirectives).toEqual([]);
    expect(snapshot.status.warnings).toEqual([]);
  });

  it("parses a conservative executable subset from user rules", async () => {
    const root = await makeWorkspaceRoot(
      [
        "- Always answer in Korean.",
        "- Include page numbers when citing sources.",
        "- No profanity.",
        "- Be concise.",
      ].join("\n"),
    );
    const kernel = new PolicyKernel(new Workspace(root));

    const snapshot = await kernel.current();

    expect(snapshot.policy.responseMode.language).toBe("ko");
    expect(snapshot.policy.citations.requireSources).toBe(true);
    expect(snapshot.policy.citations.includePageNumbers).toBe(true);
    expect(snapshot.policy.responseMode.noProfanity).toBe(true);
    expect(snapshot.policy.responseMode.concise).toBe(true);
    expect(snapshot.status.userDirectives).toEqual([
      "response.language=ko",
      "citations.require_sources=true",
      "citations.include_page_numbers=true",
      "response.no_profanity=true",
      "response.concise=true",
    ]);
  });

  it("keeps unknown lines advisory and warns on conflicting language directives", async () => {
    const root = await makeWorkspaceRoot(
      [
        "- Always answer in Korean.",
        "- Always answer in English.",
        "- Use a witty tone.",
      ].join("\n"),
    );
    const kernel = new PolicyKernel(new Workspace(root));

    const snapshot = await kernel.current();

    expect(snapshot.policy.responseMode.language).toBe("en");
    expect(snapshot.status.advisoryDirectives).toEqual(["Use a witty tone."]);
    expect(snapshot.status.warnings).toContain(
      "conflicting response.language directives detected; keeping response.language=en",
    );
  });
});
