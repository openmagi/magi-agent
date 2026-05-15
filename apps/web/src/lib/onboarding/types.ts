import type { ValidRouterType } from "@/lib/constants";
import type { ModelSelection, ApiKeyMode } from "@/lib/supabase/types";

export interface OnboardingState {
  step: number;
  modelSelection: ModelSelection | null;
  routerType?: ValidRouterType;
  language: string;
  apiKeyMode: ApiKeyMode | null;
  anthropicApiKey: string | null;
  fireworksApiKey: string | null;
  openaiApiKey: string | null;
  geminiApiKey: string | null;
  codexAccessToken: string | null;
  codexRefreshToken: string | null;
  customBaseUrl: string | null;
  botPurpose: string | null;
  purposePreset: string | null;
  pricingTier?: "pro" | "pro_plus" | "max" | "flex";
  pendingDeploy?: boolean;
  /** Personality wizard: preset id OR null if custom/none */
  personalityPreset?: string | null;
  /** Personality wizard: free-form generated style text */
  customStyle?: string | null;
  /** Purpose selector category (preset bucket) */
  purposeCategory?: string | null;
}

export const ONBOARDING_STEPS = [
  { path: "/onboarding/purpose", label: "Purpose" },
  { path: "/onboarding/personality", label: "Personality" },
  { path: "/onboarding/deploy", label: "Deploy" },
] as const;
