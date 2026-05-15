import { createCipheriv, createDecipheriv, randomBytes } from "crypto";
import { env } from "@/lib/config";

const ALGORITHM = "aes-256-gcm";
const IV_LENGTH = 12; // 96-bit IV recommended for GCM
const TAG_LENGTH = 16; // 128-bit auth tag

function getEncryptionKey(): Buffer {
  return Buffer.from(env.ENCRYPTION_KEY, "hex");
}

/**
 * Encrypt a plaintext string using AES-256-GCM.
 * Returns a hex-encoded string: iv + authTag + ciphertext
 */
export function encrypt(plaintext: string): string {
  const key = getEncryptionKey();
  const iv = randomBytes(IV_LENGTH);
  const cipher = createCipheriv(ALGORITHM, key, iv);

  const encrypted = Buffer.concat([
    cipher.update(plaintext, "utf8"),
    cipher.final(),
  ]);
  const tag = cipher.getAuthTag();

  // Format: iv (12 bytes) + tag (16 bytes) + ciphertext
  return Buffer.concat([iv, tag, encrypted]).toString("hex");
}

/**
 * Decrypt a hex-encoded AES-256-GCM ciphertext.
 * Returns the original plaintext string.
 */
export function decrypt(ciphertextHex: string): string {
  const key = getEncryptionKey();
  const data = Buffer.from(ciphertextHex, "hex");

  if (data.length < IV_LENGTH + TAG_LENGTH) {
    throw new Error("Invalid ciphertext: too short");
  }

  const iv = data.subarray(0, IV_LENGTH);
  const tag = data.subarray(IV_LENGTH, IV_LENGTH + TAG_LENGTH);
  const ciphertext = data.subarray(IV_LENGTH + TAG_LENGTH);

  const decipher = createDecipheriv(ALGORITHM, key, iv);
  decipher.setAuthTag(tag);

  const decrypted = Buffer.concat([
    decipher.update(ciphertext),
    decipher.final(),
  ]);

  return decrypted.toString("utf8");
}

/**
 * Check if a string looks like an encrypted value (hex-encoded, minimum length).
 * Used to detect whether a stored value has already been encrypted.
 */
export function isEncrypted(value: string): boolean {
  const minHexLen = (IV_LENGTH + TAG_LENGTH + 1) * 2; // at least 1 byte of ciphertext
  return /^[0-9a-fA-F]+$/.test(value) && value.length >= minHexLen;
}

/**
 * Safely decrypt a value that may or may not be encrypted.
 * Returns the original string if decryption fails (plaintext migration).
 */
export function safeDecrypt(value: string): string {
  if (!value) return value;
  if (!isEncrypted(value)) return value;
  try {
    return decrypt(value);
  } catch {
    // Value is not encrypted (plaintext legacy data) — return as-is
    return value;
  }
}
