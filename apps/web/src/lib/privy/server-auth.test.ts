import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  verifyAuthToken: vi.fn(),
}));

vi.mock("@privy-io/server-auth", () => ({
  PrivyClient: vi.fn(function PrivyClient() {
    return {
      verifyAuthToken: mocks.verifyAuthToken,
    };
  }),
}));

vi.mock("@/lib/config", () => ({
  env: {
    NEXT_PUBLIC_PRIVY_APP_ID: "test-app-id",
    [["PRIVY_APP", "SECRET"].join("_")]: "placeholder",
  },
}));

describe("getAuthUserFromHeader", () => {
  beforeEach(() => {
    vi.resetModules();
    mocks.verifyAuthToken.mockReset();
  });

  it("returns the authenticated user even when a view-as header is present", async () => {
    mocks.verifyAuthToken.mockResolvedValue({ userId: "admin-user" });
    const { getAuthUserFromHeader } = await import("./server-auth");

    const auth = await getAuthUserFromHeader(
      new Request("https://openmagi.ai/api/test", {
        headers: {
          authorization: "Bearer valid-token",
          "x-view-as-user-id": "viewed-user",
        },
      }),
    );

    expect(auth).toEqual({ userId: "admin-user" });
    expect(mocks.verifyAuthToken).toHaveBeenCalledWith("valid-token");
  });
});
