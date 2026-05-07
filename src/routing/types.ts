export type RouteTier = "LIGHT" | "MEDIUM" | "HEAVY" | "DEEP" | "XDEEP";

export type RouteProvider =
  | "anthropic"
  | "openai"
  | "openai-compatible"
  | "fireworks"
  | "google";

export type RoutingMode = "off" | "hosted-proxy" | "direct";

export interface RouteThinking {
  type: "adaptive" | "disabled";
}

export interface RoutedModel {
  tier: RouteTier;
  provider: RouteProvider;
  model: string;
  thinking?: RouteThinking;
  supportsTools: boolean;
  supportsImages: boolean;
  reason: string;
}

export interface ExplicitModelRule {
  pattern: RegExp;
  tier: RouteTier;
}

export interface FastPathRule {
  id: string;
  pattern: RegExp;
  tier: RouteTier;
}

export interface RoutingProfile {
  id: string;
  classifierModel: string;
  fallbackTier: RouteTier;
  classifierPrompt: string;
  tiers: Record<RouteTier, RoutedModel>;
  explicitModelRules: ExplicitModelRule[];
  fastPaths: FastPathRule[];
}

export interface RouteDecision extends RoutedModel {
  profileId: string;
  classifierUsed: boolean;
  classifierModel: string;
  classifierRaw?: string;
  confidence: "rule" | "classifier" | "fallback";
}

export function isRouterKeyword(model: string): boolean {
  return model === "magi-smart-router/auto" || model === "big-dic-router/auto";
}
