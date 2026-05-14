/**
 * Unit tests for the `/plan`, `/onboarding`, and `/superpowers:*` slash
 * command factory.
 * Design ref: docs/plans/2026-04-20-superpowers-plugin-design.md.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {
  SUPERPOWERS_SKILLS,
  makeAllSuperpowersSkillCommands,
  makeOnboardingCommand,
  makePlanCommand,
  makeSuperpowersSkillCommand,
  readSkillBody,
} from "./superpowers.js";
import type { SlashCommandContext } from "./registry.js";
import type { Session } from "../Session.js";
import type { SseWriter } from "../transport/SseWriter.js";

function makeSse(): {
  sse: SseWriter;
  agentEvents: Array<{ type: string; delta?: string }>;
  legacy: string[];
  text: () => string;
} {
  const agentEvents: Array<{ type: string; delta?: string }> = [];
  const legacy: string[] = [];
  const sse = {
    agent: (e: { type: string; delta?: string }) => agentEvents.push(e),
    legacyDelta: (s: string) => legacy.push(s),
    legacyFinish: vi.fn(),
  } as unknown as SseWriter;
  // Synthetic assistant text now flows exclusively on `event: agent`
  // text_delta frames (see superpowers.ts emitText note). Helper joins
  // them so assertions don't need to reach into the raw event array.
  const text = (): string =>
    agentEvents
      .filter((e) => e.type === "text_delta" && typeof e.delta === "string")
      .map((e) => e.delta as string)
      .join("");
  return { sse, agentEvents, legacy, text };
}

function makeCtx(sse: SseWriter): SlashCommandContext {
  return {
    session: { setPermissionMode: vi.fn() } as unknown as Session,
    sse,
  };
}

describe("SUPERPOWERS_SKILLS catalogue", () => {
  it("exposes 14 skills in a stable order", () => {
    expect(SUPERPOWERS_SKILLS).toHaveLength(14);
    expect(SUPERPOWERS_SKILLS).toContain("brainstorming");
    expect(SUPERPOWERS_SKILLS).toContain("writing-plans");
    expect(SUPERPOWERS_SKILLS).toContain("using-superpowers");
    expect(SUPERPOWERS_SKILLS).toContain("test-driven-development");
  });
});

describe("readSkillBody", () => {
  let root: string;
  beforeEach(async () => {
    root = await fs.mkdtemp(path.join(os.tmpdir(), "sp-"));
    await fs.mkdir(path.join(root, "writing-plans"), { recursive: true });
    await fs.writeFile(
      path.join(root, "writing-plans", "SKILL.md"),
      "---\nname: writing-plans\ndescription: Use when you have a spec\n---\n\n# Writing Plans\n\nPlan first.\n",
    );
  });
  afterEach(async () => {
    await fs.rm(root, { recursive: true, force: true });
  });

  it("returns full body when the file exists", async () => {
    const body = await readSkillBody(root, "writing-plans");
    expect(body).toContain("Writing Plans");
  });
  it("returns null when directory missing", async () => {
    expect(await readSkillBody(root, "does-not-exist")).toBeNull();
  });
});

describe("/plan command", () => {
  it("sets session to plan mode and emits confirmation", async () => {
    const cmd = makePlanCommand("/unused");
    expect(cmd.name).toBe("/plan");
    const { sse, text } = makeSse();
    const ctx = makeCtx(sse);
    await cmd.handler("", ctx);
    expect(ctx.session.setPermissionMode).toHaveBeenCalledWith("plan");
    expect(text()).toContain("Plan mode is on");
  });

  it("includes task description when args provided", async () => {
    const cmd = makePlanCommand("/unused");
    const { sse, text } = makeSse();
    const ctx = makeCtx(sse);
    await cmd.handler("refactor the auth module", ctx);
    expect(text()).toContain("Task to plan: refactor the auth module");
  });
});

describe("/onboarding command", () => {
  let root: string;
  beforeEach(async () => {
    root = await fs.mkdtemp(path.join(os.tmpdir(), "sp-onb-"));
    await fs.mkdir(path.join(root, "using-superpowers"), { recursive: true });
    await fs.writeFile(
      path.join(root, "using-superpowers", "SKILL.md"),
      "---\nname: using-superpowers\n---\n\nUSING SUPERPOWERS BODY",
    );
  });
  afterEach(async () => {
    await fs.rm(root, { recursive: true, force: true });
  });

  it("emits the Korean intro line + using-superpowers body", async () => {
    const cmd = makeOnboardingCommand(root);
    expect(cmd.name).toBe("/onboarding");
    const { sse, legacy, text } = makeSse();
    await cmd.handler("", makeCtx(sse));
    const joined = text();
    expect(joined).toContain("온보딩");
    expect(joined).toContain("USING SUPERPOWERS BODY");
    expect(legacy).toHaveLength(0);
  });
});

describe("/superpowers:<skill> commands", () => {
  let root: string;
  beforeEach(async () => {
    root = await fs.mkdtemp(path.join(os.tmpdir(), "sp-ns-"));
    await fs.mkdir(path.join(root, "brainstorming"), { recursive: true });
    await fs.writeFile(
      path.join(root, "brainstorming", "SKILL.md"),
      "---\nname: brainstorming\n---\n\nBRAINSTORM BODY",
    );
  });
  afterEach(async () => {
    await fs.rm(root, { recursive: true, force: true });
  });

  it("namespaced invocation: /superpowers:brainstorming emits body", async () => {
    const cmd = makeSuperpowersSkillCommand(root, "brainstorming");
    expect(cmd.name).toBe("/superpowers:brainstorming");
    const { sse, legacy, text } = makeSse();
    await cmd.handler("", makeCtx(sse));
    expect(text()).toContain("BRAINSTORM BODY");
    expect(legacy).toHaveLength(0);
  });

  it("factory builds one command per catalogue entry, all namespaced", () => {
    const cmds = makeAllSuperpowersSkillCommands(root);
    expect(cmds).toHaveLength(SUPERPOWERS_SKILLS.length);
    for (const c of cmds) {
      expect(c.name.startsWith("/superpowers:")).toBe(true);
    }
    expect(cmds.map((c) => c.name)).toContain("/superpowers:brainstorming");
    expect(cmds.map((c) => c.name)).toContain(
      "/superpowers:writing-plans",
    );
  });

  it("missing skill body falls open with a short pointer", async () => {
    const cmd = makeSuperpowersSkillCommand(
      path.join(root, "nope"),
      "brainstorming",
    );
    const { sse, legacy, text } = makeSse();
    await cmd.handler("", makeCtx(sse));
    const joined = text();
    expect(joined).toContain("superpowers:brainstorming");
    expect(joined).toContain("body not bundled");
    expect(legacy).toHaveLength(0);
  });
});
