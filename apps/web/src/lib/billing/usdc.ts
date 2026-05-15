import {
  createPublicClient,
  http,
  decodeEventLog,
  parseAbi,
  type Hex,
  type Address,
} from "viem";
import { base } from "viem/chains";

// ── Constants ────────────────────────────────────────────────────────
export const USDC_CONTRACT: Address =
  "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913";
export const RECEIVING_WALLET: Address =
  "0x6a2f675f5f81909eecd1966a15c90877bc106858";
export const BASE_CHAIN_ID = 8453;
export const USDC_DECIMALS = 6;
export const MIN_CONFIRMATIONS = 5;

/** 1 USDC (1_000_000 raw) = 100 credit cents ($1.00) */
export const USDC_RAW_PER_CREDIT_CENT = BigInt(10_000);

const ERC20_TRANSFER_ABI = parseAbi([
  "event Transfer(address indexed from, address indexed to, uint256 value)",
]);

// ── Public client (server-side only) ─────────────────────────────────
const publicClient = createPublicClient({
  chain: base,
  transport: http(process.env.BASE_RPC_URL || "https://mainnet.base.org"),
});

// ── Types ────────────────────────────────────────────────────────────
export interface UsdcVerificationResult {
  valid: boolean;
  amountUsdcRaw: bigint;
  amountCreditCents: number;
  from: Address;
  error?: string;
}

function fail(error: string): UsdcVerificationResult {
  return {
    valid: false,
    amountUsdcRaw: BigInt(0),
    amountCreditCents: 0,
    from: "0x0" as Address,
    error,
  };
}

/** Convert USDC raw units to credit cents (integer, truncated). */
export function usdcRawToCreditCents(raw: bigint): number {
  return Number(raw / USDC_RAW_PER_CREDIT_CENT);
}

// ── On-chain verification ────────────────────────────────────────────
export async function verifyUsdcTransaction(
  txHash: Hex,
): Promise<UsdcVerificationResult> {
  const receipt = await publicClient.getTransactionReceipt({ hash: txHash });

  if (receipt.status !== "success") {
    return fail("Transaction reverted on-chain");
  }

  const currentBlock = await publicClient.getBlockNumber();
  const confirmations = currentBlock - receipt.blockNumber;
  if (confirmations < BigInt(MIN_CONFIRMATIONS)) {
    return fail(
      `Insufficient confirmations: ${confirmations}/${MIN_CONFIRMATIONS}`,
    );
  }

  // Find USDC Transfer event to our receiving wallet
  const transferLogs = receipt.logs.filter(
    (log) => log.address.toLowerCase() === USDC_CONTRACT.toLowerCase(),
  );

  for (const log of transferLogs) {
    try {
      const decoded = decodeEventLog({
        abi: ERC20_TRANSFER_ABI,
        data: log.data,
        topics: log.topics,
      });

      if (decoded.eventName !== "Transfer") continue;

      const to = (decoded.args as { to: Address }).to;
      if (to.toLowerCase() !== RECEIVING_WALLET.toLowerCase()) continue;

      const value = (decoded.args as { value: bigint }).value;
      const amountCreditCents = usdcRawToCreditCents(value);

      if (amountCreditCents < 100) {
        return fail("Minimum USDC payment is $1.00");
      }

      return {
        valid: true,
        amountUsdcRaw: value,
        amountCreditCents,
        from: (decoded.args as { from: Address }).from,
      };
    } catch {
      continue;
    }
  }

  return fail("No valid USDC transfer to receiving wallet found in this transaction");
}
