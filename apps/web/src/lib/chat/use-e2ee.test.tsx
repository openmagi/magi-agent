import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { useE2EE, type E2EEHook } from "./use-e2ee";

describe("useE2EE OSS stub", () => {
  it("exposes no-op history methods expected by the chat view", async () => {
    let hook: E2EEHook;

    function Probe() {
      hook = useE2EE("local");
      return null;
    }

    renderToStaticMarkup(<Probe />);

    expect(hook.ready).toBe(true);
    expect(typeof hook.saveMessages).toBe("function");
    expect(typeof hook.loadMessages).toBe("function");
    expect(typeof hook.deleteMessages).toBe("function");
    await expect(hook.loadMessages("default")).resolves.toEqual({
      messages: [],
      deletions: [],
      hasMore: false,
      nextBefore: null,
    });
  });
});
