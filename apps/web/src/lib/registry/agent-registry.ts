/**
 * ERC-8004 Agent Registry integration.
 * Registers and deregisters agents on the on-chain agent registry
 * using the bot's Privy wallet via viem.
 */

import { sendTransaction } from "@/lib/privy/wallet-service";
import {
  createPublicClient,
  http,
  encodeFunctionData,
  type Address,
} from "viem";
import { base } from "viem/chains";

// ERC-8004 Agent Registry contract on Base mainnet
export const AGENT_REGISTRY_ADDRESS: Address =
  "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432";

// Minimal ABI for register/deregister
const REGISTRY_ABI = [
  {
    name: "register",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "agentURI", type: "string" },
    ],
    outputs: [
      { name: "agentId", type: "uint256" },
    ],
  },
  {
    name: "deregister",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "agentId", type: "uint256" },
    ],
    outputs: [],
  },
  {
    name: "agentURIs",
    type: "function",
    stateMutability: "view",
    inputs: [
      { name: "agentId", type: "uint256" },
    ],
    outputs: [
      { name: "", type: "string" },
    ],
  },
] as const;

const publicClient = createPublicClient({
  chain: base,
  transport: http(process.env.BASE_RPC_URL || "https://mainnet.base.org"),
});

export interface RegistryResult {
  txHash: string;
  agentId: string | null;
}

/**
 * Register an agent on the ERC-8004 registry.
 * The agentURI should point to the SKILL.md URL.
 */
export async function registerAgent(
  walletId: string,
  agentURI: string,
): Promise<RegistryResult> {
  const calldata = encodeFunctionData({
    abi: REGISTRY_ABI,
    functionName: "register",
    args: [agentURI],
  });

  const txHash = await sendTransaction(walletId, {
    to: AGENT_REGISTRY_ADDRESS,
    data: calldata,
    chainId: 8453,
  });

  // Try to extract agentId from the transaction receipt
  let agentId: string | null = null;
  try {
    const receipt = await publicClient.waitForTransactionReceipt({
      hash: txHash as `0x${string}`,
      timeout: 30_000,
    });

    // The register function returns agentId — extract from logs
    // Registration event typically emits the agentId
    if (receipt.logs.length > 0) {
      // The agentId is typically the first topic or in the log data
      const registrationLog = receipt.logs[0];
      if (registrationLog.topics[1]) {
        agentId = BigInt(registrationLog.topics[1]).toString();
      }
    }
  } catch {
    // Receipt parsing failed — agentId will be null, but tx was submitted
  }

  return { txHash, agentId };
}

/**
 * Deregister an agent from the ERC-8004 registry.
 */
export async function deregisterAgent(
  walletId: string,
  agentId: string,
): Promise<string> {
  const calldata = encodeFunctionData({
    abi: REGISTRY_ABI,
    functionName: "deregister",
    args: [BigInt(agentId)],
  });

  return sendTransaction(walletId, {
    to: AGENT_REGISTRY_ADDRESS,
    data: calldata,
    chainId: 8453,
  });
}

/**
 * Query the registry for an agent's URI.
 */
export async function getAgentURI(agentId: string): Promise<string | null> {
  try {
    const result = await publicClient.readContract({
      address: AGENT_REGISTRY_ADDRESS,
      abi: REGISTRY_ABI,
      functionName: "agentURIs",
      args: [BigInt(agentId)],
    });
    return result || null;
  } catch {
    return null;
  }
}
