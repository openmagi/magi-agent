import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const pageUrl = new URL("./page.tsx", import.meta.url);

function pageSource(): string {
  return existsSync(pageUrl) ? readFileSync(pageUrl, "utf8") : "";
}

describe("dashboard bot CLI guide page", () => {
  it("renders a bot-scoped cloud CLI guide with current bot placeholders", () => {
    const source = pageSource();

    expect(source).toContain("Cloud CLI");
    expect(source).toContain("params: Promise<{ botId: string }>");
    expect(source).toContain("const { botId } = await params;");
    expect(source).toContain("OPEN_MAGI_BOT_ID");
    expect(source).toContain("{botId}");
    expect(source).toContain("npx openmagi@latest cloud chat");
    expect(source).toContain("npx openmagi@latest cloud run");
    expect(source).toContain("/dashboard/${botId}/chat");
    expect(source).toContain("/docs/cli");
  });

  it("documents the current chat-proxy SSE boundary without exposing runtime secrets", () => {
    const source = pageSource();

    expect(source).toContain("https://openmagi.ai/api/cli/chat/$OPEN_MAGI_BOT_ID/completions");
    expect(source).toContain("<cloud-cli-access-token>");
    expect(source).toContain("opens a browser");
    expect(source).toContain("device login");
    expect(source).toContain("local loopback callback");
    expect(source).toContain("proxies to the");
    expect(source).toContain("hosted runtime without exposing bot gateway tokens");
    expect(source).toContain("Do not");
    expect(source).toContain("extracting tokens from the web app");
    expect(source).toContain("Do not copy Privy access");
    expect(source).toContain("tokens from browser developer tools");
    expect(source).toContain("agent:main:app:$OPEN_MAGI_CHANNEL");
    expect(source).toContain("Authorization: Bearer <cloud-cli-access-token>");
    expect(source).toContain("x-openmagi-channel: $OPEN_MAGI_CHANNEL");
    expect(source).not.toContain("x-openclaw-session-key: agent:main:app:$OPEN_MAGI_CHANNEL");
    expect(source).not.toContain("OPEN_MAGI_TOKEN");
    expect(source).not.toContain("<user-session-token>");
    expect(source).not.toContain("Authorization: Bearer $OPEN_MAGI_TOKEN");
    expect(source).toContain("Do not use bot gateway tokens");
    expect(source).toContain("Do not paste provider API keys");
    expect(source).not.toContain("GATEWAY_TOKEN=");
    expect(source).not.toContain("ANTHROPIC_API_KEY=");
    expect(source).not.toContain("OPENAI_API_KEY=");
  });
});
