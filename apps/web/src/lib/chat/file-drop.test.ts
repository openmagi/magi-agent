import { describe, expect, it, vi } from "vitest";
import { shouldHandlePageFileDrop } from "./file-drop";

function makeDropEvent(defaultPrevented: boolean) {
  return {
    defaultPrevented,
    preventDefault: vi.fn(),
  };
}

describe("chat file drop handling", () => {
  it("skips page attachment when a composer already handled the drop", () => {
    const event = makeDropEvent(true);

    expect(shouldHandlePageFileDrop(event)).toBe(false);
    expect(event.preventDefault).toHaveBeenCalledOnce();
  });

  it("handles page-level drops when no child handled them first", () => {
    const event = makeDropEvent(false);

    expect(shouldHandlePageFileDrop(event)).toBe(true);
    expect(event.preventDefault).toHaveBeenCalledOnce();
  });
});
