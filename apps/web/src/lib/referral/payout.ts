import {
  createWalletClient,
  createPublicClient,
  http,
  encodeFunctionData,
  parseAbi,
  type Hex,
  type Address,
} from "viem";
import { base } from "viem/chains";
import { privateKeyToAccount } from "viem/accounts";
import { USDC_CONTRACT, USDC_DECIMALS } from "@/lib/billing/usdc";

const ERC20_TRANSFER_ABI = parseAbi([
  "function transfer(address to, uint256 amount) returns (bool)",
]);

export interface PayoutResult {
  success: boolean;
  txHash?: string;
  error?: string;
}

export async function sendUsdcPayout(
  destinationAddress: string,
  amountCents: number,
): Promise<PayoutResult> {
  const privateKey = process.env.PAYOUT_WALLET_PRIVATE_KEY;
  if (!privateKey) {
    return { success: false, error: "Payout wallet not configured" };
  }

  const account = privateKeyToAccount(privateKey as Hex);

  const publicClient = createPublicClient({
    chain: base,
    transport: http(process.env.BASE_RPC_URL || "https://mainnet.base.org"),
  });

  const walletClient = createWalletClient({
    account,
    chain: base,
    transport: http(process.env.BASE_RPC_URL || "https://mainnet.base.org"),
  });

  const amountRaw = BigInt(amountCents) * BigInt(10 ** USDC_DECIMALS) / BigInt(100);

  const data = encodeFunctionData({
    abi: ERC20_TRANSFER_ABI,
    functionName: "transfer",
    args: [destinationAddress as Address, amountRaw],
  });

  try {
    const txHash = await walletClient.sendTransaction({
      to: USDC_CONTRACT,
      data,
    });

    const receipt = await publicClient.waitForTransactionReceipt({
      hash: txHash,
      confirmations: 3,
    });

    if (receipt.status === "success") {
      return { success: true, txHash };
    }
    return { success: false, txHash, error: "Transaction reverted" };
  } catch (err) {
    return { success: false, error: String(err) };
  }
}
