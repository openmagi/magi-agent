/**
 * x402 Protocol payment service.
 * Handles 402 Payment Required responses by parsing requirements,
 * executing USDC transfers via Privy wallet, and generating payment proofs.
 */

import {
  decodePaymentRequiredHeader,
  encodePaymentSignatureHeader,
} from "@x402/core/http";
import type { PaymentRequired, PaymentRequirements } from "@x402/core/types";
import { toClientEvmSigner } from "@x402/evm";
import { x402Client } from "@x402/core/client";
import { ExactEvmScheme } from "@x402/evm";
import { createAdminClient } from "@/lib/supabase/admin";
import { getWallet, signTypedData } from "@/lib/privy/wallet-service";
import { createPublicClient, http } from "viem";
import { base } from "viem/chains";
import { USDC_CONTRACT } from "@/lib/billing/usdc";

// Base chain public client
const publicClient = createPublicClient({
  chain: base,
  transport: http(process.env.BASE_RPC_URL || "https://mainnet.base.org"),
});

export interface X402PaymentInput {
  paymentRequiredHeader: string;
  targetUrl: string;
}

export interface X402PaymentResult {
  paymentHeader: string;
  txHash: string | null;
  amountUsdc: string;
}

export interface X402ValidationContext {
  botId: string;
  maxAmountUsdc?: string | number | null;
}

const BASE_NETWORK = "eip155:8453";
const DEFAULT_X402_MAX_AMOUNT_USDC = "10";

/**
 * Parse a Payment-Required header to extract payment requirements.
 */
export function parsePaymentRequired(header: string): PaymentRequired {
  return decodePaymentRequiredHeader(header);
}

/**
 * Select the best payment requirement (prefer USDC on Base).
 */
export function selectRequirement(
  paymentRequired: PaymentRequired,
): PaymentRequirements | null {
  const reqs = paymentRequired.accepts;
  if (!reqs || reqs.length === 0) return null;

  return reqs.find(
    (r) =>
      r.network === BASE_NETWORK &&
      r.asset?.toLowerCase() === USDC_CONTRACT.toLowerCase(),
  ) ?? null;
}

function requirementField(requirement: PaymentRequirements, key: string): unknown {
  return (requirement as unknown as Record<string, unknown>)[key];
}

function parseUsdcToRaw(value: string | number): bigint {
  const normalized = String(value).trim();
  if (!/^\d+(\.\d{1,6})?$/.test(normalized)) {
    throw new Error(`Invalid x402 USDC amount: ${normalized}`);
  }
  const [whole, fraction = ""] = normalized.split(".");
  return BigInt(whole) * BigInt(1_000_000) + BigInt(fraction.padEnd(6, "0"));
}

function rawRequirementAmount(requirement: PaymentRequirements): bigint {
  const raw = requirementField(requirement, "amount") ??
    requirementField(requirement, "maxAmountRequired");
  if (typeof raw !== "string" && typeof raw !== "number" && typeof raw !== "bigint") {
    throw new Error("x402 requirement is missing an amount");
  }
  const amount = BigInt(raw);
  if (amount <= BigInt(0)) throw new Error("x402 amount must be positive");
  return amount;
}

function normalizedBotMaxEnvKey(botId: string): string {
  const normalized = botId.replace(/[^a-zA-Z0-9]/g, "_").toUpperCase();
  return `X402_MAX_AMOUNT_USDC_${normalized}`;
}

function maxAmountRaw(context: X402ValidationContext): bigint {
  const value = context.maxAmountUsdc ??
    process.env[normalizedBotMaxEnvKey(context.botId)] ??
    process.env.X402_MAX_AMOUNT_USDC ??
    DEFAULT_X402_MAX_AMOUNT_USDC;
  return parseUsdcToRaw(value);
}

function normalizeAddressList(value: string | undefined): Set<string> {
  return new Set(
    (value ?? "")
      .split(",")
      .map((entry) => entry.trim().toLowerCase())
      .filter(Boolean),
  );
}

function requiredAddressList(envName: string): Set<string> {
  const allowed = normalizeAddressList(process.env[envName]);
  if (allowed.size === 0) {
    throw new Error(`${envName} must be configured before x402 payments are enabled`);
  }
  return allowed;
}

function requiredDomainList(envName: string): Set<string> {
  const allowed = new Set(
    (process.env[envName] ?? "")
      .split(",")
      .map((entry) => entry.trim().toLowerCase())
      .filter(Boolean),
  );
  if (allowed.size === 0) {
    throw new Error(`${envName} must be configured before x402 payments are enabled`);
  }
  return allowed;
}

function normalizeUrlForBinding(value: string): string {
  const url = new URL(value);
  url.hash = "";
  return url.toString();
}

export function validateX402Requirement(
  requirement: PaymentRequirements,
  targetUrl: string,
  context: X402ValidationContext,
): void {
  if (requirement.network !== BASE_NETWORK) {
    throw new Error("x402 requirement must use Base network");
  }
  if (requirement.asset?.toLowerCase() !== USDC_CONTRACT.toLowerCase()) {
    throw new Error("x402 requirement must use Base USDC");
  }

  const amount = rawRequirementAmount(requirement);
  const maxAmount = maxAmountRaw(context);
  if (amount > maxAmount) {
    throw new Error("x402 amount exceeds allowed maximum");
  }

  const allowedPayTo = normalizeAddressList(process.env.X402_ALLOWED_PAY_TO);
  const payTo = requirementField(requirement, "payTo");
  const requiredPayTo = allowedPayTo.size > 0
    ? allowedPayTo
    : requiredAddressList("X402_ALLOWED_PAY_TO");
  if (typeof payTo !== "string" || !requiredPayTo.has(payTo.toLowerCase())) {
    throw new Error("x402 payTo is not allowed");
  }

  const target = new URL(targetUrl);
  const allowedDomains = requiredDomainList("X402_ALLOWED_DOMAINS");
  if (!allowedDomains.has(target.hostname.toLowerCase())) {
    throw new Error("x402 target domain is not allowed");
  }

  const resource = requirementField(requirement, "resource");
  if (typeof resource !== "string" || resource.length === 0) {
    throw new Error("x402 requirement is missing resource binding");
  }
  if (normalizeUrlForBinding(resource) !== normalizeUrlForBinding(targetUrl)) {
    throw new Error("x402 resource binding does not match target URL");
  }
}

/**
 * Execute an x402 payment using the bot's Privy wallet.
 *
 * This uses the @x402/core client and @x402/evm scheme to create
 * a proper x402 payment payload (EIP-3009 transferWithAuthorization or Permit2).
 * The Privy wallet signs the authorization via a custom signer adapter.
 */
export async function executeX402Payment(
  botId: string,
  walletId: string,
  input: X402PaymentInput,
): Promise<X402PaymentResult> {
  // Parse the payment required header
  console.log(`[x402] Starting payment for bot=${botId} wallet=${walletId} url=${input.targetUrl}`);

  let paymentRequired: PaymentRequired;
  try {
    paymentRequired = parsePaymentRequired(input.paymentRequiredHeader);
  } catch (err) {
    console.error("[x402] Failed to parse payment header:", err);
    throw err;
  }

  const requirement = selectRequirement(paymentRequired);
  if (!requirement) {
    throw new Error("No compatible payment requirement found (need Base chain USDC)");
  }
  validateX402Requirement(requirement, input.targetUrl, { botId });
  console.log(`[x402] Requirement: network=${requirement.network} amount=${requirement.amount}`);

  // Get wallet info
  let wallet;
  try {
    wallet = await getWallet(walletId);
    console.log(`[x402] Wallet loaded: ${wallet.address}`);
  } catch (err) {
    console.error("[x402] getWallet failed:", err);
    throw err;
  }

  // Create a Privy-compatible signer for x402
  const signer = toClientEvmSigner({
    address: wallet.address as `0x${string}`,
    signTypedData: async (typedData) => {
      console.log("[x402] Signing typed data...");
      const signature = await signTypedData(walletId, typedData);
      console.log("[x402] Signed OK");
      return signature as `0x${string}`;
    },
  }, publicClient);

  // Create x402 client with EVM exact scheme for Base
  const evmScheme = new ExactEvmScheme(signer);

  const client = new x402Client();
  client.register("eip155:8453", evmScheme);

  // Create the payment payload
  let paymentPayload;
  try {
    paymentPayload = await client.createPaymentPayload(paymentRequired);
    console.log("[x402] Payment payload created OK");
  } catch (err) {
    console.error("[x402] createPaymentPayload failed:", err);
    throw err;
  }

  // Encode as HTTP header
  const paymentHeader = encodePaymentSignatureHeader(paymentPayload);

  // Extract amount for logging
  const amountUsdc = requirement.amount || "0";

  // Log payment to database
  const supabase = createAdminClient();
  const nonce = (paymentPayload.payload as Record<string, Record<string, string>>)
    ?.authorization?.nonce;
  await supabase.from("bot_x402_payments").insert({
    bot_id: botId,
    target_url: input.targetUrl,
    amount_usdc: amountUsdc,
    tx_hash: nonce || "pre-auth",
    status: "completed",
  });

  console.log(`[x402] Payment completed: ${amountUsdc} USDC`);

  return {
    paymentHeader,
    txHash: null, // x402 uses authorization-based payments, not direct transfers
    amountUsdc,
  };
}
