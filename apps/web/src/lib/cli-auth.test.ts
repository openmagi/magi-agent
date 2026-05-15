import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/config", () => ({
  env: {
    ENCRYPTION_KEY: "a".repeat(64),
  },
}));

describe("cloud CLI auth tokens", () => {
  it("mints a short-lived token scoped to one user and bot", async () => {
    const { mintCloudCliToken, verifyCloudCliToken } = await import("./cli-auth");

    const token = await mintCloudCliToken({
      userId: "did:privy:user-1",
      botId: "bot-1",
      now: new Date("2026-05-10T00:00:00.000Z"),
    });
    const claims = await verifyCloudCliToken(token, {
      now: new Date("2026-05-10T01:00:00.000Z"),
    });

    expect(claims).toEqual({
      userId: "did:privy:user-1",
      botId: "bot-1",
      scope: "cloud-cli",
    });
    expect(token).not.toContain("did:privy:user-1");
    expect(token).not.toContain("bot-1");
  });

  it("rejects expired tokens and tokens for a different bot", async () => {
    const { assertCloudCliBotAccess, mintCloudCliToken, verifyCloudCliToken } =
      await import("./cli-auth");

    const token = await mintCloudCliToken({
      userId: "did:privy:user-1",
      botId: "bot-1",
      now: new Date("2026-05-10T00:00:00.000Z"),
      ttlSeconds: 60,
    });

    await expect(
      verifyCloudCliToken(token, { now: new Date("2026-05-10T00:02:00.000Z") }),
    ).rejects.toThrow("Cloud CLI token expired");

    const claims = await verifyCloudCliToken(token, {
      now: new Date("2026-05-10T00:00:30.000Z"),
    });

    expect(() => assertCloudCliBotAccess(claims, "bot-2")).toThrow(
      "Cloud CLI token is not valid for this bot",
    );
  });
});
