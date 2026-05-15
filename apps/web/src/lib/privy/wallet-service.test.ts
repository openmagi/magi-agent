import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock the config module before importing wallet-service
vi.mock("@/lib/config", () => ({
  env: {
    NEXT_PUBLIC_PRIVY_APP_ID: "test-app-id",
    PRIVY_APP_SECRET: "test-app-secret",
    PRIVY_AUTHORIZATION_KEY_ID: "test-auth-key-id",
  },
}));

// Mock global fetch
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

import {
  createAgentWallet,
  createWalletPolicy,
  attachPolicyToWallet,
  getWallet,
  deletePolicy,
  buildDefaultPolicy,
} from "./wallet-service";

describe("wallet-service", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  describe("createAgentWallet", () => {
    it("creates a wallet via Privy API and returns id + address", async () => {
      mockFetch.mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            id: "wallet-123",
            address: "0xabc123",
            chain_type: "ethereum",
          }),
          { status: 200 },
        ),
      );

      const result = await createAgentWallet("ethereum");
      expect(result).toEqual({
        id: "wallet-123",
        address: "0xabc123",
        chainType: "ethereum",
      });
      expect(mockFetch).toHaveBeenCalledWith(
        "https://api.privy.io/v1/wallets",
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({
            "Content-Type": "application/json",
            "privy-app-id": "test-app-id",
          }),
        }),
      );
    });

    it("sends correct auth header with base64 encoded credentials", async () => {
      mockFetch.mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            id: "wallet-123",
            address: "0xabc123",
            chain_type: "ethereum",
          }),
          { status: 200 },
        ),
      );

      await createAgentWallet("ethereum");

      const expectedAuth = `Basic ${Buffer.from("test-app-id:test-app-secret").toString("base64")}`;
      const callArgs = mockFetch.mock.calls[0] as [string, RequestInit];
      const headers = callArgs[1].headers as Record<string, string>;
      expect(headers.Authorization).toBe(expectedAuth);
    });

    it("sends owner_id from env in request body", async () => {
      mockFetch.mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            id: "wallet-123",
            address: "0xabc123",
            chain_type: "ethereum",
          }),
          { status: 200 },
        ),
      );

      await createAgentWallet("ethereum");

      const callArgs = mockFetch.mock.calls[0] as [string, RequestInit];
      const body = JSON.parse(callArgs[1].body as string) as Record<string, unknown>;
      expect(body.owner_id).toBe("test-auth-key-id");
      expect(body.chain_type).toBe("ethereum");
    });

    it("throws on API error", async () => {
      mockFetch.mockResolvedValueOnce(
        new Response(JSON.stringify({ error: "bad request" }), { status: 400 }),
      );

      await expect(createAgentWallet("ethereum")).rejects.toThrow(
        "Privy API error (400)",
      );
    });

    it("defaults to ethereum chain type", async () => {
      mockFetch.mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            id: "wallet-123",
            address: "0xabc123",
            chain_type: "ethereum",
          }),
          { status: 200 },
        ),
      );

      await createAgentWallet();

      const callArgs = mockFetch.mock.calls[0] as [string, RequestInit];
      const body = JSON.parse(callArgs[1].body as string) as Record<string, unknown>;
      expect(body.chain_type).toBe("ethereum");
    });
  });

  describe("createWalletPolicy", () => {
    it("creates a policy and returns the policy id", async () => {
      mockFetch.mockResolvedValueOnce(
        new Response(JSON.stringify({ id: "policy-456" }), { status: 200 }),
      );

      const result = await createWalletPolicy({
        name: "test-policy",
        version: "1.0",
        chain_type: "ethereum",
        rules: [
          {
            name: "Spending limit",
            method: "eth_sendTransaction",
            conditions: [
              {
                field_source: "ethereum_transaction",
                field: "value",
                operator: "lte",
                value: "100000000000000000",
              },
            ],
            action: "ALLOW",
          },
        ],
      });
      expect(result.id).toBe("policy-456");
    });

    it("sends policy data as POST to /v1/policies", async () => {
      mockFetch.mockResolvedValueOnce(
        new Response(JSON.stringify({ id: "policy-456" }), { status: 200 }),
      );

      await createWalletPolicy({
        name: "test-policy",
        version: "1.0",
        chain_type: "ethereum",
        rules: [
          {
            name: "Deny all",
            method: "eth_sendTransaction",
            conditions: [],
            action: "DENY",
          },
        ],
      });

      expect(mockFetch).toHaveBeenCalledWith(
        "https://api.privy.io/v1/policies",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  describe("getWallet", () => {
    it("returns wallet details", async () => {
      mockFetch.mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            id: "wallet-123",
            address: "0xabc123",
            chain_type: "ethereum",
          }),
          { status: 200 },
        ),
      );

      const result = await getWallet("wallet-123");
      expect(result.id).toBe("wallet-123");
      expect(result.address).toBe("0xabc123");
      expect(result.chainType).toBe("ethereum");
    });

    it("calls correct endpoint with wallet id", async () => {
      mockFetch.mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            id: "wallet-xyz",
            address: "0xdef456",
            chain_type: "solana",
          }),
          { status: 200 },
        ),
      );

      await getWallet("wallet-xyz");
      expect(mockFetch).toHaveBeenCalledWith(
        "https://api.privy.io/v1/wallets/wallet-xyz",
        expect.objectContaining({ method: "GET" }),
      );
    });
  });

  describe("attachPolicyToWallet", () => {
    it("sends policy id to wallet policies endpoint", async () => {
      mockFetch.mockResolvedValueOnce(
        new Response(JSON.stringify({}), { status: 200 }),
      );

      await attachPolicyToWallet("wallet-123", "policy-456");

      expect(mockFetch).toHaveBeenCalledWith(
        "https://api.privy.io/v1/wallets/wallet-123/policies",
        expect.objectContaining({ method: "POST" }),
      );

      const callArgs = mockFetch.mock.calls[0] as [string, RequestInit];
      const body = JSON.parse(callArgs[1].body as string) as Record<string, unknown>;
      expect(body.policy_ids).toEqual(["policy-456"]);
    });
  });

  describe("deletePolicy", () => {
    it("sends DELETE to correct policy endpoint", async () => {
      mockFetch.mockResolvedValueOnce(
        new Response(null, { status: 204 }),
      );

      await deletePolicy("policy-456");

      expect(mockFetch).toHaveBeenCalledWith(
        "https://api.privy.io/v1/policies/policy-456",
        expect.objectContaining({ method: "DELETE" }),
      );
    });
  });

  describe("buildDefaultPolicy", () => {
    it("returns a default policy config for the given bot id", () => {
      const policy = buildDefaultPolicy("bot-abc");

      expect(policy.name).toBe("clawy-bot-abc-default");
      expect(policy.version).toBe("1.0");
      expect(policy.chain_type).toBe("ethereum");
      expect(policy.rules).toHaveLength(3);
      expect(policy.rules[0].method).toBe("eth_sendTransaction");
      expect(policy.rules[0].action).toBe("ALLOW");
      expect(policy.rules[1].method).toBe("eth_signTypedData_v4");
      expect(policy.rules[1].action).toBe("ALLOW");
      expect(policy.rules[2].method).toBe("personal_sign");
      expect(policy.rules[2].action).toBe("ALLOW");
    });

    it("includes 0.1 ETH max spending condition", () => {
      const policy = buildDefaultPolicy("bot-abc");

      const spendingCondition = policy.rules[0].conditions.find(
        (c) => c.field === "value",
      );
      expect(spendingCondition).toEqual({
        field_source: "ethereum_transaction",
        field: "value",
        operator: "lte",
        value: "100000000000000000", // 0.1 ETH in wei
      });
    });

    it("includes Base chain only restriction", () => {
      const policy = buildDefaultPolicy("bot-abc");

      const chainCondition = policy.rules[0].conditions.find(
        (c) => c.field === "chain_id",
      );
      expect(chainCondition).toEqual({
        field_source: "ethereum_transaction",
        field: "chain_id",
        operator: "eq",
        value: "8453", // Base chain ID
      });
    });
  });
});
