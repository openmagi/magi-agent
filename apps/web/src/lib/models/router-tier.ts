import { MODEL_LABELS, type ValidRouterType } from "@/lib/constants";
import type { ModelSelection } from "@/lib/supabase/types";

export type RouterPickerMode = "standard_router" | "premium_router" | "advanced";

export const DEFAULT_ADVANCED_MODEL: ModelSelection = "opus";
const ROUTER_MODEL_SELECTIONS = new Set<string>([
  "magi_smart_routing",
  "smart_routing",
  "gpt_smart_routing",
]);

export const ROUTER_PICKER_OPTIONS: Array<{
  value: RouterPickerMode;
  label: string;
  description: string;
}> = [
  {
    value: "standard_router",
    label: "Standard Router",
    description: "Cost-aware routing for everyday work.",
  },
  {
    value: "premium_router",
    label: "Premium Router",
    description: "Frontier routing for demanding work.",
  },
  {
    value: "advanced",
    label: "Custom",
    description: "Pick a specific model manually.",
  },
];

export function getRouterPickerMode(
  modelSelection: string | null | undefined,
  routerType: string | null | undefined,
): RouterPickerMode {
  if (modelSelection === "magi_smart_routing" && routerType === "big_dic") {
    return "premium_router";
  }
  if (modelSelection === "magi_smart_routing" && (!routerType || routerType === "standard")) {
    return "standard_router";
  }
  return "advanced";
}

export function applyRouterPickerMode(
  mode: RouterPickerMode,
  advancedModel: ModelSelection = DEFAULT_ADVANCED_MODEL,
): { modelSelection: ModelSelection; routerType: ValidRouterType } {
  if (mode === "standard_router") {
    return { modelSelection: "magi_smart_routing", routerType: "standard" };
  }
  if (mode === "premium_router") {
    return { modelSelection: "magi_smart_routing", routerType: "big_dic" };
  }
  return {
    modelSelection: ROUTER_MODEL_SELECTIONS.has(advancedModel) ? DEFAULT_ADVANCED_MODEL : advancedModel,
    routerType: "standard",
  };
}

export function getRouterDisplayName(
  modelSelection: string | null | undefined,
  routerType: string | null | undefined,
): string {
  const mode = getRouterPickerMode(modelSelection, routerType);
  if (mode === "standard_router") return "Standard Router";
  if (mode === "premium_router") return "Premium Router";
  return MODEL_LABELS[String(modelSelection || "")] || String(modelSelection || "Custom");
}
