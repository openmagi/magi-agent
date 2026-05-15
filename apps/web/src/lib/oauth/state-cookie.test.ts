import { describe, expect, it } from "vitest";

import {
  consumeOAuthStateCookie,
  createOAuthStateCookie,
  resetOAuthStateForTests,
} from "./state-cookie";

describe("OAuth state cookie", () => {
  it("keeps provider state out of the front-channel state and rejects replay", () => {
    resetOAuthStateForTests();
    const issued = createOAuthStateCookie({
      provider: "twitter",
      userId: "user-1",
      ttlSeconds: 600,
      data: { codeVerifier: "pkce-secret", writeAccess: true },
      secret: "test-secret",
      now: 1_000,
    });

    expect(issued.state).not.toContain("user-1");
    expect(issued.state).not.toContain("pkce-secret");
    expect(issued.cookie).toMatchObject({
      name: "clawy_oauth_twitter",
      httpOnly: true,
      sameSite: "lax",
      secure: true,
      path: "/api/integrations/twitter/callback",
      maxAge: 600,
    });

    const cookieHeader = `${issued.cookie.name}=${issued.cookie.value}`;
    const consumed = consumeOAuthStateCookie({
      provider: "twitter",
      state: issued.state,
      cookieHeader,
      secret: "test-secret",
      now: 1_000,
    });

    expect(consumed).toEqual({
      userId: "user-1",
      data: { codeVerifier: "pkce-secret", writeAccess: true },
    });
    expect(consumeOAuthStateCookie({
      provider: "twitter",
      state: issued.state,
      cookieHeader,
      secret: "test-secret",
      now: 1_000,
    })).toBeNull();
  });

  it("rejects expired cookies", () => {
    resetOAuthStateForTests();
    const issued = createOAuthStateCookie({
      provider: "google",
      userId: "user-1",
      ttlSeconds: 300,
      data: { includeAds: true },
      secret: "test-secret",
      now: 1_000,
    });

    expect(consumeOAuthStateCookie({
      provider: "google",
      state: issued.state,
      cookieHeader: `${issued.cookie.name}=${issued.cookie.value}`,
      secret: "test-secret",
      now: 301_001,
    })).toBeNull();
  });
});
