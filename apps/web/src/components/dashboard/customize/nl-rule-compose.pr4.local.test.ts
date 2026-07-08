/**
 * PR-4 (policies-first authoring consolidation) — source-grep tests for the
 * NL intent threading: every natural-language save path passes the operator's
 * ORIGINAL sentence (`intent`) plus a derived/compiler-suggested
 * `displayName` through putCustomRule so the server's auto-promoted 1-rule
 * Policy carries the user's own words. Same source-grep pattern as the
 * sibling .local.test.ts files (the throwaway vitest config does not resolve
 * the `@/` alias, so components are not executed).
 */

import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";


const SRC = readFileSync(
  new URL("./nl-rule-compose.tsx", import.meta.url),
  "utf-8",
);

const CONV = readFileSync(
  new URL("./conversational-compose.tsx", import.meta.url),
  "utf-8",
);

const API = readFileSync(
  new URL("../../../lib/customize-api.ts", import.meta.url),
  "utf-8",
);


describe("customize-api: policy-envelope seam (PR-4)", () => {
  it("putCustomRule accepts an optional CustomRulePolicyEnvelope and merges it into the PUT body", () => {
    expect(API).toContain("export interface CustomRulePolicyEnvelope");
    expect(API).toContain("policyEnvelope?: CustomRulePolicyEnvelope");
    expect(API).toContain("body.displayName = policyEnvelope.displayName");
    expect(API).toContain("body.intent = policyEnvelope.intent");
  });

  it("exposes upsertPolicy (PUT /v1/app/policies/{id}) for the hybrid one-policy save", () => {
    expect(API).toContain("export async function upsertPolicy(");
    expect(API).toContain("export interface UpsertPolicyInput");
    expect(API).toMatch(/\/v1\/app\/policies\/\$\{encodeURIComponent\(policyId\)\}`,\s*\{\s*method: "PUT"/);
  });
});


describe("ConversationalCompose: save meta carries the original NL sentence", () => {
  it("onSave receives ConversationalSaveMeta with intentText from the FIRST user turn", () => {
    expect(CONV).toContain("export interface ConversationalSaveMeta");
    expect(CONV).toContain("meta: ConversationalSaveMeta");
    expect(CONV).toMatch(
      /history\.find\(\(t\) => t\.role === "user"\)[\s\S]{0,200}intentText/,
    );
  });
});


describe("NlRuleCompose: intent threading on every NL save path (PR-4)", () => {
  it("exports derivePolicyDisplayName (word-boundary trim near 60 chars)", () => {
    expect(SRC).toContain("export function derivePolicyDisplayName(");
    expect(SRC).toContain('replace(/\\s+/g, " ")');
    expect(SRC).toContain("collapsed.length <= 60");
  });

  it("conversational save threads meta.intentText into putCustomRule's envelope", () => {
    expect(SRC).toContain("onSave={async (draft, meta) => {");
    expect(SRC).toMatch(
      /meta\.intentText\.trim\(\)[\s\S]{0,600}displayName: derivePolicyDisplayName\(intentText\),\s*intent: intentText/,
    );
  });

  it("one-shot Activate threads the textarea sentence (nlText) into the envelope", () => {
    expect(SRC).toMatch(
      /const intentText = nlText\.trim\(\);[\s\S]{0,400}result\.draft as CustomRule/,
    );
  });

  it("architect single proposal uses the compiler summary as displayName + nlText as intent", () => {
    expect(SRC).toMatch(
      /derivePolicyDisplayName\(\s*proposal\.summary\.trim\(\) \|\| intentText,?\s*\)/,
    );
    expect(SRC).toContain("activatePrimitive(agentFetch, primitive, null, envelope)");
  });

  it("hybrid proposal saves ONE policy: member rules under a groupId + explicit upsertPolicy", () => {
    // Server-side per-rule auto-promotion is skipped for grouped saves; the
    // client upserts the single Policy (id = groupId) referencing every
    // member so the composition renders as one card.
    expect(SRC).toContain("upsertPolicy(agentFetch, groupId, {");
    expect(SRC).toContain("ruleIds: memberIds");
    expect(SRC).toContain("intent: intentText");
    // Members without a compiler-supplied id get a client-minted cr_ id so
    // the policy can reference them.
    expect(SRC).toContain("function newRuleId()");
  });

  it("Guided/Raw stay envelope-free (no fabricated intent) — only the CustomRule route carries it", () => {
    // activatePrimitive forwards the envelope ONLY on the CustomRule branch;
    // seam_spec / custom_check saves have no envelope parameter.
    expect(SRC).toContain("policyEnvelope?: CustomRulePolicyEnvelope");
    expect(SRC).toContain("putCustomRule(agentFetch, rule, policyEnvelope)");
    expect(SRC).not.toMatch(/putSeamSpec\([^)]*policyEnvelope/);
    expect(SRC).not.toMatch(/putDashboardCheck\([^)]*policyEnvelope/);
  });
});
