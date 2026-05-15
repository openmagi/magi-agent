import { PrivyClient } from "@privy-io/server-auth";
import { env } from "@/lib/config";

// ── Types ───────────────────────────────────────────────────────────────────

const PRIVY_API_BASE = "https://api.privy.io/v1";

// Lazy-initialized SDK client (handles authorization signatures automatically)
let _privyClient: PrivyClient | null = null;
function getPrivyClient(): PrivyClient {
  if (!_privyClient) {
    _privyClient = new PrivyClient(
      env.NEXT_PUBLIC_PRIVY_APP_ID,
      env.PRIVY_APP_SECRET,
      {
        walletApi: {
          authorizationPrivateKey: env.PRIVY_AUTHORIZATION_KEY_PRIVATE,
        },
      },
    );
  }
  return _privyClient;
}

export interface PrivyWalletResult {
  id: string;
  address: string;
  chainType: string;
}

/** Mirrors the Privy API policy condition shape (snake_case preserved for direct pass-through). */
export interface PolicyCondition {
  field_source: string;
  field: string;
  operator: string;
  value: string | string[];
}

export interface PolicyRule {
  name: string;
  method: string;
  conditions: PolicyCondition[];
  action: "ALLOW" | "DENY";
}

export interface PolicyInput {
  name: string;
  version: "1.0";
  chain_type: "ethereum" | "solana";
  rules: PolicyRule[];
}

export interface PrivyPolicyResult {
  id: string;
}

// ── Internal helper ─────────────────────────────────────────────────────────

interface PrivyWalletApiResponse {
  id: string;
  address: string;
  chain_type: string;
}

async function privyRequest<T>(
  path: string,
  method: "GET" | "POST" | "PUT" | "DELETE",
  body?: unknown,
): Promise<T> {
  const url = `${PRIVY_API_BASE}${path}`;
  const credentials = Buffer.from(
    `${env.NEXT_PUBLIC_PRIVY_APP_ID}:${env.PRIVY_APP_SECRET}`,
  ).toString("base64");

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "privy-app-id": env.NEXT_PUBLIC_PRIVY_APP_ID,
    Authorization: `Basic ${credentials}`,
  };

  const res = await fetch(url, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!res.ok) {
    const errBody = await res.text();
    throw new Error(`Privy API error (${res.status}): ${errBody}`);
  }

  // DELETE responses may have no body
  if (res.status === 204) {
    return undefined as T;
  }

  return res.json() as Promise<T>;
}

// ── Public API ──────────────────────────────────────────────────────────────

export async function createAgentWallet(
  chainType: "ethereum" | "solana" = "ethereum",
): Promise<PrivyWalletResult> {
  const data = await privyRequest<PrivyWalletApiResponse>("/wallets", "POST", {
    chain_type: chainType,
    owner_id: env.PRIVY_AUTHORIZATION_KEY_ID,
  });
  return { id: data.id, address: data.address, chainType: data.chain_type };
}

export async function getWallet(walletId: string): Promise<PrivyWalletResult> {
  const data = await privyRequest<PrivyWalletApiResponse>(
    `/wallets/${encodeURIComponent(walletId)}`,
    "GET",
  );
  return { id: data.id, address: data.address, chainType: data.chain_type };
}

export async function createWalletPolicy(
  input: PolicyInput,
): Promise<PrivyPolicyResult> {
  return privyRequest<PrivyPolicyResult>("/policies", "POST", input);
}

export async function attachPolicyToWallet(
  walletId: string,
  policyId: string,
): Promise<void> {
  await privyRequest(`/wallets/${encodeURIComponent(walletId)}/policies`, "POST", {
    policy_ids: [policyId],
  });
}

export async function deletePolicy(policyId: string): Promise<void> {
  await getPrivyClient().walletApi.deletePolicy({ id: policyId });
}

/** Attempts Privy's wallet DELETE endpoint; current apps may still reject this API. */
export async function deleteWallet(walletId: string): Promise<void> {
  await privyRequest(`/wallets/${encodeURIComponent(walletId)}`, "DELETE", {});
}

// ── RPC Operations ──────────────────────────────────────────────────────────

interface PrivyRpcResponse {
  data: {
    method: string;
    params: unknown[];
    result?: string;
  };
}

/** Sign a message using the Privy wallet (personal_sign). */
export async function signMessage(
  walletId: string,
  message: string,
): Promise<string> {
  const hexMessage = message.startsWith("0x")
    ? message
    : `0x${Buffer.from(message, "utf-8").toString("hex")}`;

  const res = await privyRequest<PrivyRpcResponse>(
    `/wallets/${encodeURIComponent(walletId)}/rpc`,
    "POST",
    {
      method: "personal_sign",
      params: {
        message: hexMessage,
      },
    },
  );

  const signature = res.data?.result;
  if (!signature) {
    throw new Error("Privy personal_sign returned no result");
  }
  return signature;
}

/** Send a transaction using the Privy wallet (eth_sendTransaction). */
export async function sendTransaction(
  walletId: string,
  tx: {
    to: string;
    value?: string;
    data?: string;
    chainId?: number;
  },
): Promise<string> {
  const res = await privyRequest<PrivyRpcResponse>(
    `/wallets/${encodeURIComponent(walletId)}/rpc`,
    "POST",
    {
      method: "eth_sendTransaction",
      caip2: `eip155:${tx.chainId ?? 8453}`,
      params: {
        transaction: {
          to: tx.to,
          value: tx.value ?? "0",
          ...(tx.data ? { data: tx.data } : {}),
        },
      },
    },
  );

  const txHash = res.data?.result;
  if (!txHash) {
    throw new Error("Privy eth_sendTransaction returned no result");
  }
  return txHash;
}

/** Sign typed data using the Privy wallet (eth_signTypedData_v4).
 *  Uses the SDK to handle authorization signatures and field normalization. */
export async function signTypedData(
  walletId: string,
  typedData: unknown,
): Promise<string> {
  // Normalize BigInt values to strings before passing to SDK
  const raw = typeof typedData === "string"
    ? JSON.parse(typedData) as Record<string, unknown>
    : JSON.parse(JSON.stringify(typedData, (_key, value) =>
        typeof value === "bigint" ? value.toString() : value,
      )) as Record<string, unknown>;

  const client = getPrivyClient();
  const result = await client.walletApi.rpc({
    walletId,
    method: "eth_signTypedData_v4",
    params: {
      typedData: {
        domain: raw.domain as Record<string, unknown>,
        types: raw.types as Record<string, unknown[]>,
        message: raw.message as Record<string, unknown>,
        primaryType: (raw.primaryType as string) || (raw.primary_type as string),
      },
    },
  });

  if (result.method !== "eth_signTypedData_v4" || !("data" in result)) {
    throw new Error("Privy eth_signTypedData_v4 returned unexpected response");
  }
  const signature = (result.data as { signature: string }).signature;
  if (!signature) {
    throw new Error("Privy eth_signTypedData_v4 returned no signature");
  }
  return signature;
}

export function buildDefaultPolicy(botId: string): PolicyInput {
  return {
    name: `clawy-${botId}-default`,
    version: "1.0",
    chain_type: "ethereum",
    rules: [
      {
        name: "Allow Base chain transactions up to 0.1 ETH",
        method: "eth_sendTransaction",
        conditions: [
          {
            field_source: "ethereum_transaction",
            field: "value",
            operator: "lte",
            value: "100000000000000000", // 0.1 ETH in wei
          },
          {
            field_source: "ethereum_transaction",
            field: "chain_id",
            operator: "eq",
            value: "8453", // Base chain ID
          },
        ],
        action: "ALLOW",
      },
      {
        name: "Allow EIP-712 typed data signing on Base (x402 payments)",
        method: "eth_signTypedData_v4",
        conditions: [
          {
            field_source: "ethereum_transaction",
            field: "chain_id",
            operator: "eq",
            value: "8453",
          },
        ],
        action: "ALLOW",
      },
      {
        name: "Allow personal_sign on Base",
        method: "personal_sign",
        conditions: [
          {
            field_source: "ethereum_transaction",
            field: "chain_id",
            operator: "eq",
            value: "8453",
          },
        ],
        action: "ALLOW",
      },
    ],
  };
}
