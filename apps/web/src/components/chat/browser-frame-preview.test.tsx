import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { BrowserFramePreview } from "./browser-frame-preview";
import type { BrowserFrame } from "@/lib/chat/types";

const source = readFileSync(new URL("./browser-frame-preview.tsx", import.meta.url), "utf8");

const frame: BrowserFrame = {
  action: "click",
  url: "https://example.com/app",
  imageBase64: Buffer.from("frame").toString("base64"),
  contentType: "image/png",
  capturedAt: 123,
};

describe("BrowserFramePreview", () => {
  it("renders a keyboard-accessible large-preview trigger", () => {
    const html = renderToStaticMarkup(
      <BrowserFramePreview
        frame={frame}
        surface="work-console"
        className="preview"
        imageClassName="image"
      />,
    );

    expect(html).toContain("Live browser");
    expect(html).toContain("Clicking");
    expect(html).toContain("https://example.com/app");
    expect(html).toContain('data-browser-frame-expand-trigger="true"');
    expect(html).toContain('aria-label="Open larger browser preview"');
    expect(html).toContain('aria-haspopup="dialog"');
    expect(html).toContain("cursor-zoom-in");
  });

  it("includes the expanded viewer dialog and escape-key close path", () => {
    expect(source).toContain('data-browser-frame-expanded-viewer="true"');
    expect(source).toContain('role="dialog"');
    expect(source).toContain('aria-modal="true"');
    expect(source).toContain('event.key === "Escape"');
    expect(source).toContain("document.body.style.overflow = \"hidden\"");
  });
});
