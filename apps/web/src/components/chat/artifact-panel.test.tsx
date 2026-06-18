import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ArtifactCard, ArtifactPanel, type ArtifactRef } from "./artifact-panel";

const source = readFileSync(new URL("./artifact-panel.tsx", import.meta.url), "utf8");

const digest = (char: string) => `sha256:${char.repeat(64)}`;
const githubTokenShape = ["g", "hp", "_", "a".repeat(36)].join("");
const openAiKeyShape = ["s", "k", "-", "b".repeat(48)].join("");
const jwtShape = [
  "eyJhbGciOiJIUzI1NiJ9",
  "eyJzdWIiOiJvcGVubWFnaSJ9",
  "c2lnbmF0dXJl",
].join(".");
const awsKeyShape = ["A", "K", "IA", "C".repeat(16)].join("");
const authTokenName = ["auth", "Token"].join("");
const sessionTokenName = ["session", "Token"].join("");
const apiKeyName = ["api", "_", "key"].join("");
const bearerOpaqueShape = ["bear", "er", ":", "opaque_abc123"].join("");
const bearerOpaqueTokenShape = ["bear", "er", ":", "opaque", "_token_abc123"].join("");
const refreshTokenShape = ["refresh", "_token_abc123"].join("");
const oauthShape = ["oa", "uth", ":", "opaque_abc123"].join("");
const slackTokenShape = ["x", "ox", "b", "-", "1".repeat(24)].join("");
const compactJwtShape = [
  "eyJhbGciOiJIUzI1NiJ9",
  "e30",
  "abcdefghijkl",
].join(".");

type ArtifactWithProductPlane = ArtifactRef & {
  productPlaneArtifact?: Record<string, unknown>;
};

function artifact(
  productPlaneArtifact?: Record<string, unknown>,
): ArtifactWithProductPlane {
  return {
    id: "attachment_public_html",
    filename: "analysis.html",
    botId: "bot_public",
    ...(productPlaneArtifact ? { productPlaneArtifact } : {}),
  };
}

function cardMarkup(productPlaneArtifact?: Record<string, unknown>): string {
  return renderToStaticMarkup(
    <ArtifactCard artifact={artifact(productPlaneArtifact)} onOpen={() => {}} />,
  );
}

describe("ArtifactCard product-plane receipts", () => {
  it("warns on missing delivery receipt without claiming delivery", () => {
    const html = cardMarkup({
      artifactId: "artifact:html-report",
      artifactDigest: digest("a"),
      renderStatus: "rendered",
      renderReceiptId: "receipt:render-ok",
      deliveryStatus: "delivered",
    });

    expect(html).toContain("Artifact Digest");
    expect(html).toContain("sha256:aaaaaa…aaaaaa");
    expect(html).toContain("Render Verified");
    expect(html).toContain("Delivery Receipt Missing");
    expect(html).toContain("missing_delivery_receipt");
    expect(html).not.toMatch(/>\s*Delivered\s*</);
  });

  it("shows failed render status", () => {
    const html = cardMarkup({
      artifactId: "artifact:html-report",
      renderStatus: "failed",
      deliveryStatus: "pending",
      warningCodes: ["render_sandbox_failed"],
    });

    expect(html).toContain("Render Failed");
    expect(html).toContain("render_sandbox_failed");
    expect(html).not.toContain("Render Verified");
  });

  it("shows verified render only when backed by a receipt or digest", () => {
    const html = cardMarkup({
      artifactId: "artifact:html-report",
      artifactDigest: digest("b"),
      renderStatus: "rendered",
      renderReceiptId: "receipt:render-ok",
      renderDigest: digest("c"),
      deliveryStatus: "pending",
    });

    expect(html).toContain("Render Verified");
    expect(html).toContain("receipt:render-ok");
    expect(html).toContain("sha256:cccccc…cccccc");
    expect(html).not.toContain("Render Receipt Missing");
  });

  it("shows delivered only when backed by a delivery receipt or digest", () => {
    const html = cardMarkup({
      artifactId: "artifact:html-report",
      artifactDigest: digest("d"),
      renderStatus: "rendered",
      renderDigest: digest("e"),
      deliveryStatus: "delivered",
      deliveryReceiptId: "receipt:telegram-delivery",
      deliveryDigest: digest("f"),
    });

    expect(html).toMatch(/>\s*Delivered\s*</);
    expect(html).toContain("receipt:telegram-delivery");
    expect(html).toContain("sha256:ffffff…ffffff");
    expect(html).not.toContain("Delivery Receipt Missing");
  });

  it("shows unsupported delivery warning", () => {
    const html = cardMarkup({
      artifactId: "artifact:html-report",
      renderStatus: "rendered",
      renderDigest: digest("1"),
      deliveryStatus: "pending",
      unsupportedDelivery: true,
      warningCodes: ["delivery_backend_deferred"],
    });

    expect(html).toContain("Delivery Unsupported");
    expect(html).toContain("delivery_backend_deferred");
  });

  it("does not render unsafe product-plane artifact metadata", () => {
    const unsafePrompt = ["raw", "Prompt"].join("");
    const unsafeOutput = ["raw", "Model", "Output"].join("");
    const unsafeAuth = ["auth", "Header"].join("");
    const unsafeCookie = ["session", "Cookie"].join("");
    const unsafeToken = ["connector", "Token"].join("");
    const unsafePath = ["var", "lib", "openmagi", "runtime-state.db"].join("/");

    const html = cardMarkup({
      artifactId: `artifact:${unsafePath}`,
      artifactDigest: digest("2"),
      renderStatus: "rendered",
      renderReceiptId: `receipt:${unsafeAuth}`,
      renderDigest: digest("3"),
      deliveryStatus: "delivered",
      deliveryReceiptId: `receipt:${unsafeToken}`,
      deliveryDigest: digest("4"),
      warningCodes: ["safe_warning", unsafeCookie, unsafePath],
      [unsafePrompt]: "hidden instruction text",
      [unsafeOutput]: "private model output",
    });

    expect(html).toContain("safe_warning");
    expect(html).toContain("sha256:222222…222222");
    expect(html).not.toContain(unsafePath);
    expect(html).not.toContain(unsafeAuth);
    expect(html).not.toContain(unsafeCookie);
    expect(html).not.toContain(unsafeToken);
    expect(html).not.toContain("hidden instruction text");
    expect(html).not.toContain("private model output");
    expect(html).not.toMatch(/rawPrompt|rawModelOutput|authHeader|sessionCookie|connectorToken/i);
  });

  it("redacts secret-shaped IDs, receipt IDs, and reason codes", () => {
    const html = cardMarkup({
      artifactId: `artifact:${authTokenName}_abc123`,
      artifactDigest: digest("7"),
      renderStatus: "rendered",
      renderReceiptId: `receipt:${apiKeyName}_live_123`,
      renderDigest: digest("8"),
      deliveryStatus: "delivered",
      deliveryReceiptId: `receipt:${sessionTokenName}_abc`,
      deliveryDigest: digest("9"),
      warningCodes: [
        "safe_warning",
        githubTokenShape,
        openAiKeyShape,
        jwtShape,
        awsKeyShape,
        `${authTokenName}_reason`,
        `${apiKeyName}_reason`,
        `${sessionTokenName}_reason`,
      ],
    });

    expect(html).toContain("safe_warning");
    expect(html).toContain("sha256:777777…777777");
    expect(html).not.toContain(authTokenName);
    expect(html).not.toContain(apiKeyName);
    expect(html).not.toContain(sessionTokenName);
    expect(html).not.toContain(githubTokenShape);
    expect(html).not.toContain(openAiKeyShape);
    expect(html).not.toContain(jwtShape);
    expect(html).not.toContain(awsKeyShape);
    expect(html).not.toContain("receipt:api");
    expect(html).not.toContain("receipt:session");
  });

  it("renders no product-plane receipt group for an empty projection object", () => {
    const html = cardMarkup({});

    expect(html).not.toContain("Product-plane artifact receipts");
    expect(html).not.toContain('data-product-plane-artifact-status="true"');
    expect(html).not.toContain("Render Pending");
    expect(html).not.toContain("Delivery Pending");
  });

  it("renders no product-plane receipt group for unsafe-only projection metadata", () => {
    const unsafePrompt = ["raw", "Prompt"].join("");
    const unsafeCookie = ["session", "Cookie"].join("");
    const unsafePath = ["var", "lib", "openmagi", "runtime-state.db"].join("/");

    const html = cardMarkup({
      artifactId: `artifact:${unsafePath}`,
      renderReceiptId: `receipt:${authTokenName}_abc`,
      deliveryReceiptId: `receipt:${apiKeyName}_abc`,
      warningCodes: [unsafeCookie, githubTokenShape],
      [unsafePrompt]: "hidden runtime prompt",
    });

    expect(html).not.toContain("Product-plane artifact receipts");
    expect(html).not.toContain('data-product-plane-artifact-status="true"');
    expect(html).not.toContain(unsafePath);
    expect(html).not.toContain(authTokenName);
    expect(html).not.toContain(apiKeyName);
    expect(html).not.toContain(unsafeCookie);
    expect(html).not.toContain(githubTokenShape);
    expect(html).not.toContain("hidden runtime prompt");
  });

  it("redacts prefixed JWT-shaped artifact and receipt IDs", () => {
    const html = cardMarkup({
      artifactId: `artifact:${jwtShape}`,
      artifactDigest: digest("0"),
      renderStatus: "rendered",
      renderReceiptId: `receipt:${jwtShape}`,
      renderDigest: digest("1"),
      deliveryStatus: "delivered",
      deliveryReceiptId: `receipt:${jwtShape}`,
      deliveryDigest: digest("2"),
    });

    expect(html).toContain("Artifact Digest");
    expect(html).toContain("Render Verified");
    expect(html).toMatch(/>\s*Delivered\s*</);
    expect(html).not.toContain(jwtShape);
    expect(html).not.toContain("artifact:eyJ");
    expect(html).not.toContain("receipt:eyJ");
  });

  it("redacts bearer, refresh, oauth, slack, and compact JWT bypass IDs", () => {
    const html = cardMarkup({
      artifactId: `artifact:${compactJwtShape}`,
      artifactDigest: digest("3"),
      renderStatus: "rendered",
      renderReceiptId: `receipt:${bearerOpaqueShape}`,
      renderDigest: digest("4"),
      deliveryStatus: "delivered",
      deliveryReceiptId: `receipt:${slackTokenShape}`,
      deliveryDigest: digest("5"),
      warningCodes: [
        "safe_warning",
        bearerOpaqueTokenShape,
        refreshTokenShape,
        oauthShape,
        compactJwtShape,
        slackTokenShape,
      ],
    });

    expect(html).toContain("safe_warning");
    expect(html).toContain("sha256:333333…333333");
    expect(html).toContain("Render Verified");
    expect(html).toMatch(/>\s*Delivered\s*</);
    expect(html).not.toContain(bearerOpaqueShape);
    expect(html).not.toContain(bearerOpaqueTokenShape);
    expect(html).not.toContain(refreshTokenShape);
    expect(html).not.toContain(oauthShape);
    expect(html).not.toContain(slackTokenShape);
    expect(html).not.toContain(compactJwtShape);
    expect(html).not.toContain("receipt:bearer");
    expect(html).not.toContain("receipt:xox");
    expect(html).not.toContain("artifact:eyJ");
  });

  it("does not infer render or delivery success from receipts without explicit status", () => {
    const html = cardMarkup({
      artifactId: "artifact:html-report",
      artifactDigest: digest("a"),
      renderReceiptId: "receipt:render-ok",
      renderDigest: digest("b"),
      deliveryReceiptId: "receipt:delivery-ok",
      deliveryDigest: digest("c"),
    });

    expect(html).toContain("Render Pending");
    expect(html).toContain("Delivery Pending");
    expect(html).toContain("receipt:render-ok");
    expect(html).toContain("receipt:delivery-ok");
    expect(html).not.toContain("Render Verified");
    expect(html).not.toMatch(/>\s*Delivered\s*</);
  });

  it("does not infer success when status is invalid even if receipts exist", () => {
    const html = cardMarkup({
      artifactId: "artifact:html-report",
      artifactDigest: digest("d"),
      renderStatus: "complete",
      renderReceiptId: "receipt:render-ok",
      deliveryStatus: "success",
      deliveryReceiptId: "receipt:delivery-ok",
      deliveryDigest: digest("e"),
    });

    expect(html).toContain("Render Pending");
    expect(html).toContain("Delivery Pending");
    expect(html).not.toContain("Render Verified");
    expect(html).not.toMatch(/>\s*Delivered\s*</);
  });

  it("shows delivery receipt pending for rendered delivery status without receipt", () => {
    const html = cardMarkup({
      artifactId: "artifact:html-report",
      renderStatus: "rendered",
      renderDigest: digest("f"),
      deliveryStatus: "rendered",
    });

    expect(html).toContain("Delivery Receipt Pending");
    expect(html).not.toContain("Delivery Rendered");
  });
});

describe("ArtifactPanel sandbox affordances", () => {
  it("renders product-plane receipt summary in the panel header", () => {
    const html = renderToStaticMarkup(
      <ArtifactPanel
        artifact={artifact({
          artifactDigest: digest("5"),
          renderStatus: "rendered",
          renderDigest: digest("6"),
          deliveryStatus: "delivered",
          deliveryReceiptId: "receipt:panel-delivery",
        })}
        onClose={() => {}}
      />,
    );

    expect(html).toContain("Product-plane artifact receipts");
    expect(html).toContain("Render Verified");
    expect(html).toMatch(/>\s*Delivered\s*</);
    expect(html).toContain("receipt:panel-delivery");
    expect(html).toContain("sha256:555555…555555");
    expect(html).toContain('role="group"');
  });

  it("keeps existing sandboxed panel actions available", () => {
    const html = renderToStaticMarkup(
      <ArtifactPanel artifact={artifact()} onClose={() => {}} />,
    );

    expect(html).toContain('role="dialog"');
    expect(html).toContain('aria-label="HTML artifact"');
    expect(html).toContain("Sandboxed HTML preview");
    expect(html).toContain('aria-label="Open raw"');
    expect(html).toContain('aria-label="Close"');
    expect(html).toContain("/v1/chat/bot_public/attachments/attachment_public_html");
  });

  it("keeps iframe rendering sandboxed without source text exposure", () => {
    const sandboxAttribute = source.match(/sandbox="([^"]*)"/)?.[1] ?? "";

    expect(sandboxAttribute).toBe("allow-scripts");
    expect(sandboxAttribute).not.toContain("allow-same-origin");
    expect(source).not.toMatch(/dangerouslySetInnerHTML|<pre|<code/);
  });
});
