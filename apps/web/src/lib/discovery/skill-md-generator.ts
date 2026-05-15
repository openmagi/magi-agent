/**
 * Generates SKILL.md content for bot Agent App Discovery.
 * Follows the /.well-known/agents/{botId}/SKILL.md convention.
 */

export interface SkillMdInput {
  botName: string;
  botPurpose: string | null;
  botId: string;
  walletAddress: string | null;
  registryAgentId: string | null;
}

export function generateSkillMd(input: SkillMdInput): string {
  const purpose = input.botPurpose || "AI agent deployed on openmagi.ai";
  const walletLine = input.walletAddress
    ? `- Wallet: ${input.walletAddress}`
    : "- Wallet: (not provisioned)";
  const registryLine = input.registryAgentId
    ? `- Registry: ERC-8004 #${input.registryAgentId}`
    : "- Registry: (not registered)";

  return `# ${input.botName}
${purpose}

## Endpoints
### POST /v1/chat/${input.botId}/completions
- Auth: Bearer token or SIWA
- Format: OpenAI chat completion compatible
- Streaming: supported

## Identity
${walletLine}
- Chain: Base (8453)
${registryLine}

## Platform
openmagi.ai — AI agent deployment platform
`;
}
