/**
 * identityInjector unit tests — T3-17.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import {
  makeIdentityInjectorHook,
  buildIdentityFence,
  enforceCap,
  loadSections,
  MAX_CHARS,
} from "./identityInjector.js";
import type { HookContext } from "../types.js";
import type { LLMClient, LLMMessage, LLMToolDef } from "../../transport/LLMClient.js";
import type { AgentEvent } from "../../transport/SseWriter.js";

function makeLLMStub(): LLMClient {
  return {} as unknown as LLMClient;
}

function makeCtx(): {
  ctx: HookContext;
  emitted: AgentEvent[];
  logs: Array<{ level: string; msg: string; data?: object }>;
} {
  const emitted: AgentEvent[] = [];
  const logs: Array<{ level: string; msg: string; data?: object }> = [];
  const ctx: HookContext = {
    botId: "bot-test",
    userId: "user-test",
    sessionKey: "session-test",
    turnId: "turn-test",
    llm: makeLLMStub(),
    transcript: [],
    emit: (e) => emitted.push(e),
    log: (level, msg, data) => logs.push({ level, msg, data }),
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
  };
  return { ctx, emitted, logs };
}

function makeArgs(
  system = "BASE_SYSTEM",
  iteration = 0,
): {
  messages: LLMMessage[];
  tools: LLMToolDef[];
  system: string;
  iteration: number;
} {
  const messages: LLMMessage[] = [{ role: "user", content: "hello" }];
  const tools: LLMToolDef[] = [];
  return { messages, tools, system, iteration };
}

async function mkTempWorkspace(): Promise<string> {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "identity-inject-"));
  return dir;
}

async function rmTree(dir: string): Promise<void> {
  await fs.rm(dir, { recursive: true, force: true });
}

describe("identityInjector", () => {
  let workspaceRoot: string;

  beforeEach(async () => {
    workspaceRoot = await mkTempWorkspace();
    delete process.env.MAGI_IDENTITY_INJECTION;
  });

  afterEach(async () => {
    await rmTree(workspaceRoot);
    delete process.env.MAGI_IDENTITY_INJECTION;
  });

  it("case 1: no files → noop", async () => {
    const hook = makeIdentityInjectorHook({ workspaceRoot });
    const { ctx, emitted } = makeCtx();
    const result = await hook.handler(makeArgs(), ctx);
    expect(result).toEqual({ action: "continue" });
    expect(emitted).toHaveLength(0);
  });

  it("case 2: only identity.md → injected, rules+methodology sections omitted", async () => {
    await fs.writeFile(path.join(workspaceRoot, "identity.md"), "You are Nova, a finance assistant.");
    const hook = makeIdentityInjectorHook({ workspaceRoot });
    const { ctx, emitted } = makeCtx();
    const result = await hook.handler(makeArgs("BASE"), ctx);
    if (!result) throw new Error("expected result");
    expect(result.action).toBe("replace");
    if (result.action !== "replace") throw new Error("expected replace");
    const sys = result.value.system;
    expect(sys).toContain("<agent-identity source=\"user\"");
    expect(sys).toContain("# Role");
    expect(sys).toContain("You are Nova");
    expect(sys).not.toContain("# Rules (MUST follow)");
    expect(sys).not.toContain("# Methodology");
    expect(sys.endsWith("BASE")).toBe(true);
    // Audit event emitted with sections list.
    expect(emitted).toHaveLength(1);
    const ev = emitted[0];
    expect(ev && ev.type === "rule_check" && ev.ruleId).toBe("identity-injector");
    if (ev && ev.type === "rule_check") {
      expect(ev.detail).toContain("sections=identity");
      expect(ev.detail).not.toContain("sections=identity,rules");
    }
  });

  it("case 3: all three files → all three sections present in order", async () => {
    await fs.writeFile(path.join(workspaceRoot, "identity.md"), "Role body");
    await fs.writeFile(path.join(workspaceRoot, "rules.md"), "Rule A\nRule B");
    await fs.writeFile(path.join(workspaceRoot, "soul.md"), "Be kind. Be rigorous.");
    const hook = makeIdentityInjectorHook({ workspaceRoot });
    const { ctx, emitted } = makeCtx();
    const result = await hook.handler(makeArgs("SYS"), ctx);
    if (!result || result.action !== "replace") throw new Error("expected replace");
    const sys = result.value.system;
    const roleIdx = sys.indexOf("# Role");
    const rulesIdx = sys.indexOf("# Rules (MUST follow)");
    const methodIdx = sys.indexOf("# Methodology");
    expect(roleIdx).toBeGreaterThan(-1);
    expect(rulesIdx).toBeGreaterThan(roleIdx);
    expect(methodIdx).toBeGreaterThan(rulesIdx);
    expect(sys).toContain("Role body");
    expect(sys).toContain("Rule A");
    expect(sys).toContain("Be kind");
    expect(sys.endsWith("SYS")).toBe(true);
    // Audit event — sections=identity,rules,soul.
    const ev = emitted[0];
    if (ev && ev.type === "rule_check") {
      expect(ev.detail).toContain("sections=identity,rules,soul");
    }
  });

  it("case 4: total content > cap → largest section truncated", async () => {
    const bigIdentity = "I".repeat(MAX_CHARS + 2_000);
    const smallRules = "R".repeat(50);
    const smallSoul = "S".repeat(50);
    await fs.writeFile(path.join(workspaceRoot, "identity.md"), bigIdentity);
    await fs.writeFile(path.join(workspaceRoot, "rules.md"), smallRules);
    await fs.writeFile(path.join(workspaceRoot, "soul.md"), smallSoul);
    const loaded = await loadSections(workspaceRoot);
    const capped = enforceCap(loaded);
    // Identity (largest) got clipped, rules+soul preserved.
    expect(capped.rules).toBe(smallRules);
    expect(capped.soul).toBe(smallSoul);
    expect((capped.identity ?? "").length).toBeLessThan(bigIdentity.length);
    expect(capped.identity ?? "").toContain("... [truncated]");
    const totalLen =
      (capped.identity?.length ?? 0) +
      (capped.rules?.length ?? 0) +
      (capped.soul?.length ?? 0);
    expect(totalLen).toBeLessThanOrEqual(MAX_CHARS);
  });

  it("case 5: non-first iteration → skip", async () => {
    await fs.writeFile(path.join(workspaceRoot, "identity.md"), "Role");
    const hook = makeIdentityInjectorHook({ workspaceRoot });
    const { ctx } = makeCtx();
    const result = await hook.handler(makeArgs("BASE", 1), ctx);
    expect(result).toEqual({ action: "continue" });
  });

  it("case 6: env=off → skip", async () => {
    process.env.MAGI_IDENTITY_INJECTION = "off";
    await fs.writeFile(path.join(workspaceRoot, "identity.md"), "Role");
    const hook = makeIdentityInjectorHook({ workspaceRoot });
    const { ctx, emitted } = makeCtx();
    const result = await hook.handler(makeArgs(), ctx);
    expect(result).toEqual({ action: "continue" });
    expect(emitted).toHaveLength(0);
  });

  it("case 7: audit event carries correct revision hash", async () => {
    const idBody = "Role body";
    const rulesBody = "Rule A";
    await fs.writeFile(path.join(workspaceRoot, "identity.md"), idBody);
    await fs.writeFile(path.join(workspaceRoot, "rules.md"), rulesBody);
    const hook = makeIdentityInjectorHook({ workspaceRoot });
    const { ctx, emitted } = makeCtx();
    const result = await hook.handler(makeArgs(), ctx);
    if (!result || result.action !== "replace") throw new Error("expected replace");
    // Recompute the expected revision: sha256 over key/body joined.
    const expected = crypto
      .createHash("sha256")
      .update(`identity\n${idBody}\n\nrules\n${rulesBody}\n\n`, "utf8")
      .digest("hex")
      .slice(0, 8);
    const sys = result.value.system;
    expect(sys).toContain(`revision="${expected}"`);
    const ev = emitted[0];
    if (ev && ev.type === "rule_check") {
      expect(ev.detail).toContain(`revision=${expected}`);
    } else {
      throw new Error("expected rule_check event");
    }
  });

  it("workspace config identity_injection: off disables hook", async () => {
    await fs.writeFile(path.join(workspaceRoot, "identity.md"), "Role");
    await fs.writeFile(
      path.join(workspaceRoot, "agent.config.yaml"),
      "identity_injection: off\n",
    );
    const hook = makeIdentityInjectorHook({ workspaceRoot });
    const { ctx, emitted } = makeCtx();
    const result = await hook.handler(makeArgs(), ctx);
    expect(result).toEqual({ action: "continue" });
    expect(emitted).toHaveLength(0);
  });

  it("hook metadata: priority=1, blocking=true, beforeLLMCall", () => {
    const hook = makeIdentityInjectorHook({ workspaceRoot });
    expect(hook.priority).toBe(1);
    expect(hook.blocking).toBe(true);
    expect(hook.point).toBe("beforeLLMCall");
    expect(hook.name).toBe("builtin:identity-injector");
  });

  it("buildIdentityFence returns null when all sections empty", () => {
    const result = buildIdentityFence({ identity: null, rules: null, soul: null });
    expect(result).toBeNull();
  });

  it("empty / whitespace-only files are treated as missing", async () => {
    await fs.writeFile(path.join(workspaceRoot, "identity.md"), "   \n\t\n");
    await fs.writeFile(path.join(workspaceRoot, "rules.md"), "Real rule");
    const loaded = await loadSections(workspaceRoot);
    expect(loaded.identity).toBeNull();
    expect(loaded.rules).toBe("Real rule");
  });
});
