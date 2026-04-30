import { describe, it, expect } from "vitest";
import { fileDeliveryInterceptor } from "./fileDeliveryInterceptor.js";

describe("fileDeliveryInterceptor", () => {
  it("creates a registered hook with correct metadata", () => {
    const hook = fileDeliveryInterceptor({
      workspaceRoot: "/home/ocuser/.clawy/workspace",
    });

    expect(hook.name).toBe("builtin:file-delivery-interceptor");
    expect(hook.point).toBe("beforeLLMCall");
    expect(hook.priority).toBe(1);
    expect(hook.blocking).toBe(true);
    expect(typeof hook.handler).toBe("function");
  });

  it("skips non-zero iterations", async () => {
    const hook = fileDeliveryInterceptor({
      workspaceRoot: "/home/ocuser/.clawy/workspace",
    });

    const result = await hook.handler(
      {
        messages: [{ role: "user", content: "send report.pdf" }],
        tools: [],
        system: "",
        iteration: 1,
      } as never,
      {} as never,
    );

    expect(result).toBeUndefined();
  });

  it("skips messages without file extensions", async () => {
    const hook = fileDeliveryInterceptor({
      workspaceRoot: "/home/ocuser/.clawy/workspace",
    });

    const result = await hook.handler(
      {
        messages: [{ role: "user", content: "안녕하세요 보내줘" }],
        tools: [],
        system: "",
        iteration: 0,
      } as never,
      {} as never,
    );

    expect(result).toBeUndefined();
  });

  it("skips messages longer than 500 chars", async () => {
    const hook = fileDeliveryInterceptor({
      workspaceRoot: "/home/ocuser/.clawy/workspace",
    });

    const result = await hook.handler(
      {
        messages: [{ role: "user", content: "a".repeat(501) + " report.pdf 보내줘" }],
        tools: [],
        system: "",
        iteration: 0,
      } as never,
      {} as never,
    );

    expect(result).toBeUndefined();
  });
});
