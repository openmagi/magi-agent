/**
 * Tests for ``LiveActivityIndicator``.
 *
 * Centre-bubble UX rule: replace the bare typing dots with a real activity
 * line when the assistant is in a tool-loop phase with no text/thinking yet.
 * Non-thinking models (Kimi, Fireworks routes, etc.) emit no
 * ``thinking_delta`` and produce text only AFTER the tool loop, so the
 * legacy "..." sat empty for many seconds while the Work panel was alive.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { describe, expect, it, vi } from "vitest";

import { LiveActivityIndicator } from "./live-activity-indicator";
import type { ToolActivity } from "@/chat-core";


function tool(
  label: string,
  status: ToolActivity["status"] = "running",
  startedAt = 1_000,
): ToolActivity {
  return {
    id: `${label}-${Math.random().toString(36).slice(2, 8)}`,
    label,
    status,
    startedAt,
  };
}


describe("LiveActivityIndicator", () => {
  it("falls back to the bare typing dots when no tools are running", () => {
    const html = renderToStaticMarkup(
      createElement(LiveActivityIndicator, { activeTools: [] }),
    );
    // Idle state mounts the legacy TypingIndicator (three .animate-bounce dots).
    const dotMatches = html.match(/animate-bounce/g) ?? [];
    expect(dotMatches).toHaveLength(3);
    expect(html).not.toContain('data-live-activity="working"');
  });

  it("renders a working badge with the running tool's label", () => {
    vi.spyOn(Date, "now").mockReturnValue(5_000);
    try {
      const html = renderToStaticMarkup(
        createElement(LiveActivityIndicator, {
          activeTools: [tool("WebFetch")],
        }),
      );
      expect(html).toContain('data-live-activity="working"');
      expect(html).toContain("WebFetch");
    } finally {
      vi.restoreAllMocks();
    }
  });

  it("groups duplicate tool names with a count suffix", () => {
    vi.spyOn(Date, "now").mockReturnValue(5_000);
    try {
      const html = renderToStaticMarkup(
        createElement(LiveActivityIndicator, {
          activeTools: [tool("WebFetch"), tool("WebFetch"), tool("WebFetch")],
        }),
      );
      expect(html).toContain("WebFetch ×3");
    } finally {
      vi.restoreAllMocks();
    }
  });

  it("joins multiple distinct tool names with a · separator", () => {
    vi.spyOn(Date, "now").mockReturnValue(5_000);
    try {
      const html = renderToStaticMarkup(
        createElement(LiveActivityIndicator, {
          activeTools: [tool("WebFetch"), tool("BrowserTask"), tool("TodoWrite")],
        }),
      );
      expect(html).toContain("WebFetch · BrowserTask · TodoWrite");
    } finally {
      vi.restoreAllMocks();
    }
  });

  it("ignores tools whose status is not running", () => {
    const html = renderToStaticMarkup(
      createElement(LiveActivityIndicator, {
        activeTools: [
          tool("WebFetch", "done"),
          tool("BrowserTask", "error"),
        ],
      }),
    );
    // All non-running → fall back to dots.
    const dotMatches = html.match(/animate-bounce/g) ?? [];
    expect(dotMatches).toHaveLength(3);
  });

  it("renders the elapsed counter based on the oldest running tool", () => {
    vi.spyOn(Date, "now").mockReturnValue(6_000);
    try {
      const html = renderToStaticMarkup(
        createElement(LiveActivityIndicator, {
          activeTools: [tool("WebFetch", "running", 1_000)],
        }),
      );
      // 6_000 - 1_000 = 5000ms => 5s
      expect(html).toContain("5s");
    } finally {
      vi.restoreAllMocks();
    }
  });

  it("uses Korean labels when language=ko", () => {
    vi.spyOn(Date, "now").mockReturnValue(5_000);
    try {
      const html = renderToStaticMarkup(
        createElement(LiveActivityIndicator, {
          activeTools: [tool("WebFetch")],
          language: "ko",
        }),
      );
      expect(html).toContain("작업 중");
    } finally {
      vi.restoreAllMocks();
    }
  });
});
