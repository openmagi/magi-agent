import { afterEach, beforeEach, describe, it, expect } from "vitest";
import {
  buildSiwaMessage,
  parseSiwaMessage,
  validateSiwaInput,
  verifySiwaSignature,
} from "./auth-service";

const ORIGINAL_ENV = { ...process.env };

describe("SIWA auth-service", () => {
  beforeEach(() => {
    process.env.SIWA_ALLOWED_DOMAINS = "example.com,allowed.example";
  });

  afterEach(() => {
    process.env = { ...ORIGINAL_ENV };
  });

  describe("buildSiwaMessage", () => {
    const address = "0x1234567890abcdef1234567890abcdef12345678";
    const input = {
      domain: "example.com",
      uri: "https://example.com/login",
      nonce: "nonce-123456",
    };

    it("should include the domain in the first line", () => {
      const msg = buildSiwaMessage(address, input);
      expect(msg).toContain("example.com wants you to sign in with your Ethereum account:");
    });

    it("should include the address on the second line", () => {
      const msg = buildSiwaMessage(address, input);
      const lines = msg.split("\n");
      expect(lines[1]).toBe(address);
    });

    it("should include the URI", () => {
      const msg = buildSiwaMessage(address, input);
      expect(msg).toContain("URI: https://example.com/login");
    });

    it("should include the nonce", () => {
      const msg = buildSiwaMessage(address, input);
      expect(msg).toContain("Nonce: nonce-123456");
    });

    it("should default to Base chain ID 8453", () => {
      const msg = buildSiwaMessage(address, input);
      expect(msg).toContain("Chain ID: 8453");
    });

    it("rejects non-Base chain IDs", () => {
      expect(() => buildSiwaMessage(address, { ...input, chainId: 1 })).toThrow(/chain/i);
    });

    it("should use custom statement when provided", () => {
      const msg = buildSiwaMessage(address, {
        ...input,
        statement: "Custom statement",
      });
      expect(msg).toContain("Custom statement");
      expect(msg).not.toContain("Sign in with agent wallet");
    });

    it("should use default statement when not provided", () => {
      const msg = buildSiwaMessage(address, input);
      expect(msg).toContain("Sign in with agent wallet");
    });

    it("should include Version: 1", () => {
      const msg = buildSiwaMessage(address, input);
      expect(msg).toContain("Version: 1");
    });

    it("should include Issued At timestamp", () => {
      const msg = buildSiwaMessage(address, input);
      expect(msg).toMatch(/Issued At: \d{4}-\d{2}-\d{2}T/);
    });
  });

  describe("validateSiwaInput", () => {
    it("rejects signing input when the SIWA domain allowlist is not configured", () => {
      delete process.env.SIWA_ALLOWED_DOMAINS;

      expect(() => validateSiwaInput({
        domain: "example.com",
        uri: "https://example.com/login",
        nonce: "nonce-123456",
      })).toThrow(/SIWA_ALLOWED_DOMAINS/i);
    });

    it("rejects ambiguous or untrusted signing input before wallet signing", () => {
      expect(() => validateSiwaInput({
        domain: "example.com\nattacker.test",
        uri: "https://example.com/login",
        nonce: "nonce-123456",
      })).toThrow(/invalid domain/i);

      expect(() => validateSiwaInput({
        domain: "example.com",
        uri: "https://attacker.test/login",
        nonce: "nonce-123456",
      })).toThrow(/domain.*uri/i);

      expect(() => validateSiwaInput({
        domain: "localhost",
        uri: "http://localhost/login",
        nonce: "nonce-123456",
      })).toThrow(/hostname/i);

      expect(() => validateSiwaInput({
        domain: "example.com",
        uri: "https://example.com/login",
        nonce: "nonce-123456",
        chainId: 1,
      })).toThrow(/chain/i);
    });

    it("honors an explicit SIWA domain allowlist", () => {
      expect(() => validateSiwaInput({
        domain: "example.com",
        uri: "https://example.com/login",
        nonce: "nonce-123456",
      }, { allowedDomains: ["allowed.example"] })).toThrow(/allowed/i);

      expect(() => validateSiwaInput({
        domain: "allowed.example",
        uri: "https://allowed.example/login",
        nonce: "nonce-123456",
      }, { allowedDomains: ["allowed.example"] })).not.toThrow();
    });
  });

  describe("parseSiwaMessage", () => {
    const address = "0x1234567890abcdef1234567890abcdef12345678";

    it("extracts canonical EIP-4361 fields from a generated message", () => {
      const message = buildSiwaMessage(address, {
        domain: "example.com",
        uri: "https://example.com/login",
        nonce: "nonce-123456",
      });

      const parsed = parseSiwaMessage(message);
      expect(parsed.domain).toBe("example.com");
      expect(parsed.address).toBe(address);
      expect(parsed.uri).toBe("https://example.com/login");
      expect(parsed.chainId).toBe(8453);
      expect(parsed.nonce).toBe("nonce-123456");
    });

    it("verifySiwaSignature rejects malformed policy fields before signature trust", async () => {
      const message = buildSiwaMessage(address, {
        domain: "example.com",
        uri: "https://example.com/login",
        nonce: "nonce-123456",
      });

      await expect(verifySiwaSignature(
        message.replace("URI: https://example.com/login", "URI: https://attacker.test/login"),
        "0xdead",
      )).resolves.toEqual({ valid: false, address: null });
    });
  });
});
