"use client";

/**
 * E2EE utilities for chat messages.
 * Key derived from Privy embedded wallet private key via HKDF-SHA256.
 * Encryption: AES-256-GCM with random IV per message.
 */

const SALT_V3 = new TextEncoder().encode("openmagi-chat-e2ee-v3");
const SALT_V2 = new TextEncoder().encode("clawy-chat-e2ee-v2");
const SALT_V1 = new TextEncoder().encode("clawy-chat-e2ee");
const KEY_LENGTH = 256;
export const E2EE_CIPHERTEXT_PREFIX_V3 = "openmagi-e2ee:v3:";
export const E2EE_CIPHERTEXT_PREFIX_V2 = "clawy-e2ee:v2:";

/**
 * Messages the user personal_signs on their embedded wallet to produce
 * E2EE seed material. v3 is the active cross-platform key; older
 * signatures are reused from local cache only as decrypt candidates.
 */
export const E2EE_SIGN_MESSAGE =
  "Open Magi — enable end-to-end encrypted chat history for this account. " +
  "Sign once per device; only sign this message on openmagi.ai. v3";

/**
 * Legacy key derivation used before the signed-message scheme shipped.
 * Derived the AES key straight from the wallet address + userId, which
 * made the key public-ish. Kept around so we can still decrypt old
 * messages encrypted under that scheme; never used for new writes.
 */
export async function deriveLegacyKey(
  walletAddress: string,
  userId: string,
): Promise<CryptoKey> {
  const normalized = walletAddress.toLowerCase().startsWith("0x")
    ? walletAddress.toLowerCase().slice(2)
    : walletAddress.toLowerCase();
  const seed = new TextEncoder().encode(`${normalized}:${userId}`);
  const hash = await crypto.subtle.digest("SHA-256", seed);

  const baseKey = await crypto.subtle.importKey(
    "raw",
    hash,
    "HKDF",
    false,
    ["deriveKey"],
  );

  return crypto.subtle.deriveKey(
    {
      name: "HKDF",
      hash: "SHA-256",
      salt: toArrayBuffer(SALT_V1),
      info: new TextEncoder().encode(userId),
    },
    baseKey,
    { name: "AES-GCM", length: KEY_LENGTH },
    false,
    ["encrypt", "decrypt"],
  );
}

/** Legacy signed-message key used by unprefixed web ciphertexts. */
export async function deriveLegacySignedKey(
  signatureHex: string,
  userId: string,
): Promise<CryptoKey> {
  return deriveKeyWithSalt(signatureHex, userId, SALT_V1);
}

/** Legacy v2 signed-message key used by prefixed v2 ciphertexts. */
export async function deriveLegacyV2Key(
  signatureHex: string,
  userId: string,
): Promise<CryptoKey> {
  return deriveKeyWithSalt(signatureHex, userId, SALT_V2);
}

/** Derive AES-256 key from wallet private key hex + userId */
export async function deriveKey(
  walletPrivateKeyHex: string,
  userId: string,
): Promise<CryptoKey> {
  return deriveKeyWithSalt(walletPrivateKeyHex, userId, SALT_V3);
}

async function deriveKeyWithSalt(
  walletPrivateKeyHex: string,
  userId: string,
  salt: Uint8Array,
): Promise<CryptoKey> {
  const rawKey = hexToBytes(walletPrivateKeyHex);
  const info = new TextEncoder().encode(userId);

  const baseKey = await crypto.subtle.importKey(
    "raw",
    toArrayBuffer(rawKey),
    "HKDF",
    false,
    ["deriveKey"],
  );

  return crypto.subtle.deriveKey(
    { name: "HKDF", hash: "SHA-256", salt: toArrayBuffer(salt), info },
    baseKey,
    { name: "AES-GCM", length: KEY_LENGTH },
    false,
    ["encrypt", "decrypt"],
  );
}

/** Encrypt plaintext message content */
export async function encryptMessage(
  key: CryptoKey,
  plaintext: string,
): Promise<{ encrypted: string; iv: string }> {
  const iv = crypto.getRandomValues(new Uint8Array(12)) as unknown as Uint8Array<ArrayBuffer>;
  const encoded = new TextEncoder().encode(plaintext);
  const ciphertext = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    key,
    encoded,
  );
  return {
    encrypted: `${E2EE_CIPHERTEXT_PREFIX_V3}${bytesToBase64(new Uint8Array(ciphertext))}`,
    iv: bytesToBase64(iv),
  };
}

/** Decrypt encrypted message content */
export async function decryptMessage(
  key: CryptoKey,
  encrypted: string,
  iv: string,
): Promise<string> {
  const normalized = encrypted.startsWith(E2EE_CIPHERTEXT_PREFIX_V3)
    ? encrypted.slice(E2EE_CIPHERTEXT_PREFIX_V3.length)
    : encrypted.startsWith(E2EE_CIPHERTEXT_PREFIX_V2)
      ? encrypted.slice(E2EE_CIPHERTEXT_PREFIX_V2.length)
      : encrypted;
  const cipherBytes = base64ToBytes(normalized) as unknown as Uint8Array<ArrayBuffer>;
  const ivBytes = base64ToBytes(iv) as unknown as Uint8Array<ArrayBuffer>;
  const decrypted = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: ivBytes },
    key,
    cipherBytes,
  );
  return new TextDecoder().decode(decrypted);
}

// --- Helpers ---

function hexToBytes(hex: string): Uint8Array {
  const clean = hex.startsWith("0x") ? hex.slice(2) : hex;
  const bytes = new Uint8Array(clean.length / 2);
  for (let i = 0; i < bytes.length; i++) {
    bytes[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
  }
  return bytes;
}

function bytesToBase64(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes));
}

function base64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) {
    bytes[i] = bin.charCodeAt(i);
  }
  return bytes;
}

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}
