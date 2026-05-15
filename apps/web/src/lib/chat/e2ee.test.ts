import { describe, expect, it } from "vitest";
import {
  E2EE_CIPHERTEXT_PREFIX_V2,
  E2EE_CIPHERTEXT_PREFIX_V3,
  decryptMessage,
  deriveKey,
  deriveLegacyKey,
  deriveLegacyV2Key,
  encryptMessage,
} from "./e2ee";

const SIGNATURE =
  "0x000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f";

describe("chat e2ee", () => {
  it("uses Open Magi branding for the active signing message", async () => {
    const e2ee = await import("./e2ee");

    expect(e2ee.E2EE_SIGN_MESSAGE).toContain("Open Magi");
    expect(e2ee.E2EE_SIGN_MESSAGE).toContain("openmagi.ai");
    expect(e2ee.E2EE_SIGN_MESSAGE.toLowerCase()).not.toContain("clawy");
  });

  it("prefixes new ciphertexts with the Open Magi v3 marker", async () => {
    const key = await deriveKey(SIGNATURE, "user-1");

    const encrypted = await encryptMessage(key, "hello");

    expect(encrypted.encrypted.startsWith(E2EE_CIPHERTEXT_PREFIX_V3)).toBe(true);
    await expect(decryptMessage(key, encrypted.encrypted, encrypted.iv)).resolves.toBe("hello");
  });

  it("continues to decrypt v2 ciphertexts with the provided legacy v2 key", async () => {
    const key = await deriveLegacyV2Key(SIGNATURE, "user-1");
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ciphertext = await crypto.subtle.encrypt(
      { name: "AES-GCM", iv },
      key,
      new TextEncoder().encode("v2-compatible"),
    );

    await expect(
      decryptMessage(
        key,
        `${E2EE_CIPHERTEXT_PREFIX_V2}${Buffer.from(new Uint8Array(ciphertext)).toString("base64")}`,
        Buffer.from(iv).toString("base64"),
      ),
    ).resolves.toBe("v2-compatible");
  });

  it("continues to decrypt unprefixed legacy ciphertext with the provided key", async () => {
    const key = await deriveKey(SIGNATURE, "user-1");
    const encrypted = await encryptMessage(key, "legacy-compatible");
    const unprefixed = encrypted.encrypted.slice(E2EE_CIPHERTEXT_PREFIX_V3.length);

    await expect(decryptMessage(key, unprefixed, encrypted.iv)).resolves.toBe("legacy-compatible");
  });

  it("derives the historical wallet-address legacy key from the hashed normalized address seed", async () => {
    const key = await deriveLegacyKey("0xabcdef12", "did:privy:test");
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ciphertext = await crypto.subtle.encrypt(
      { name: "AES-GCM", iv },
      key,
      new TextEncoder().encode("wallet-legacy"),
    );

    await expect(
      decryptMessage(
        key,
        Buffer.from(new Uint8Array(ciphertext)).toString("base64"),
        Buffer.from(iv).toString("base64"),
      ),
    ).resolves.toBe("wallet-legacy");
  });
});
