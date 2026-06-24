import { describe, expect, it, vi } from "vitest";

vi.mock("react", () => ({
  useCallback: (fn: unknown) => fn,
  useEffect: () => {},
  useState: (init: unknown) => [init, () => {}],
}));
vi.mock("./local-api", () => ({
  useAgentFetch: () => async () => new Response(),
}));

// eslint-disable-next-line import/order -- mocks must precede the import
import {
  compileRule,
  type ArchitectProposal,
  type InterviewQuestion,
  type RuleCompileResponse,
} from "./customize-api";


function mockJsonResponse(body: unknown, status = 200): Response {
  const ok = status >= 200 && status < 300;
  return {
    ok,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}


describe("compileRule — F-UX6 interview-mode body forwarding", () => {
  it("forwards mode='interview' on the request body when provided", async () => {
    const fetch = vi.fn().mockResolvedValue(
      mockJsonResponse({ ok: true, mode: "interview", questions: [] }),
    );
    await compileRule(
      fetch as unknown as typeof globalThis.fetch,
      "audit AWS keys",
      undefined,
      "interview",
    );
    const [path, init] = (
      fetch as unknown as { mock: { calls: [string, RequestInit][] } }
    ).mock.calls[0];
    expect(path).toBe("/v1/app/customize/rules/compile");
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({ nlText: "audit AWS keys", mode: "interview" });
  });

  it("omits mode when undefined (legacy back-compat)", async () => {
    const fetch = vi.fn().mockResolvedValue(
      mockJsonResponse({ ok: true } as RuleCompileResponse),
    );
    await compileRule(
      fetch as unknown as typeof globalThis.fetch,
      "deny shell_exec",
    );
    const [, init] = (
      fetch as unknown as { mock: { calls: [string, RequestInit][] } }
    ).mock.calls[0];
    const body = JSON.parse(init.body as string);
    expect(body.mode).toBeUndefined();
    expect(body).toEqual({ nlText: "deny shell_exec" });
  });
});


describe("compileRule — F-UX6 response-shape passthrough", () => {
  it("returns mode='interview' + questions[] verbatim", async () => {
    const q: InterviewQuestion = {
      question: "Which tool's output should we scan?",
      expects: "tool_name",
      inventory: ["FileRead", "shell_exec"],
    };
    const fetch = vi.fn().mockResolvedValue(
      mockJsonResponse({
        ok: true,
        mode: "interview",
        questions: [q],
        intent: {
          whatToCheck: "audit AWS keys",
          whereInLifecycle: "unknown",
          whatToDoOnFail: "unknown",
          openQuestions: [q],
          confidence: 0.4,
        },
      }),
    );
    const out = await compileRule(
      fetch as unknown as typeof globalThis.fetch,
      "audit AWS keys",
      undefined,
      "interview",
    );
    expect(out.mode).toBe("interview");
    expect(out.questions).toHaveLength(1);
    expect(out.questions![0].expects).toBe("tool_name");
    expect(out.intent?.confidence).toBeCloseTo(0.4);
  });

  it("returns mode='proposal' + proposal verbatim", async () => {
    const proposal: ArchitectProposal = {
      mode: "hybrid",
      primitives: [
        {
          kind: "llm_criterion",
          payload: {},
          trustClass: "advisory",
          rationale: "regex narrows critic",
        },
        {
          kind: "custom_check",
          payload: {},
          trustClass: "deterministic",
          rationale: "cheap pre-filter",
        },
      ],
      summary: "Audit AWS keys: regex + critic",
      explanation: "Hybrid composition.",
    };
    const fetch = vi.fn().mockResolvedValue(
      mockJsonResponse({ ok: true, mode: "proposal", proposal }),
    );
    const out = await compileRule(
      fetch as unknown as typeof globalThis.fetch,
      "audit AWS keys",
      undefined,
      "interview",
    );
    expect(out.mode).toBe("proposal");
    expect(out.proposal?.mode).toBe("hybrid");
    expect(out.proposal?.primitives).toHaveLength(2);
  });
});
