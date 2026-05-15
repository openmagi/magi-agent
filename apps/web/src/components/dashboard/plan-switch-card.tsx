"use client";

import { GlassCard } from "@/components/ui/glass-card";
import { Button } from "@/components/ui/button";
import { useMessages } from "@/lib/i18n";

export interface SubscriptionInfo {
  plan: string;
  status: string;
  isTrialing: boolean;
  trialStartedAt: string | null;
  trialEndsAt: string | null;
  trialExpired?: boolean;
  currentPeriodEnd: string | null;
  scheduledPlan: string | null;
  scheduledChangeAt: string | null;
}

interface PlanSwitchCardProps {
  subscription: SubscriptionInfo;
  onSwitchPlan: (targetPlan: string) => void;
  onCancelScheduled: () => void;
  cancelingScheduled: boolean;
}

const PLAN_DETAILS: Record<string, { name: string; price: string }> = {
  byok: { name: "BYOK Plan", price: "$7.99/mo" },
  pro: { name: "Pro Plan", price: "$14.99/mo" },
  pro_plus: { name: "Pro+ Plan", price: "$89.99/mo" },
};

export function PlanSwitchCard({
  subscription,
  onSwitchPlan,
  onCancelScheduled,
  cancelingScheduled,
}: PlanSwitchCardProps) {
  const t = useMessages();
  const plan = PLAN_DETAILS[subscription.plan] ?? { name: subscription.plan, price: "" };

  // Determine available target plans based on current plan
  const getTargetPlans = (): { key: string; label: string; action: string }[] => {
    switch (subscription.plan) {
      case "byok":
        return [
          { key: "pro", label: PLAN_DETAILS.pro.name, action: t.planSwitch.switchTo },
          { key: "pro_plus", label: PLAN_DETAILS.pro_plus.name, action: t.planSwitch.switchTo },
        ];
      case "pro":
        return [
          { key: "pro_plus", label: PLAN_DETAILS.pro_plus.name, action: t.planSwitch.switchTo },
          { key: "byok", label: PLAN_DETAILS.byok.name, action: t.planSwitch.switchTo },
        ];
      case "pro_plus":
        return [
          { key: "pro", label: PLAN_DETAILS.pro.name, action: t.planSwitch.switchTo },
          { key: "byok", label: PLAN_DETAILS.byok.name, action: t.planSwitch.switchTo },
        ];
      default:
        return [];
    }
  };
  const targetPlans = getTargetPlans();

  return (
    <GlassCard glow className="mb-6">
      <div className="flex items-center justify-between mb-2">
        <div>
          <p className="text-sm text-secondary mb-1">{t.planSwitch.currentPlan}</p>
          <p className="text-xl font-bold text-foreground">
            {plan.name}{" "}
            <span className="text-base font-normal text-muted">{plan.price}</span>
          </p>
        </div>
        {subscription.isTrialing && subscription.trialEndsAt && (
          <span className="px-3 py-1 rounded-full text-xs font-medium bg-violet-500/15 text-violet-400 border border-violet-500/20">
            {t.planSwitch.trial} &mdash;{" "}
            {t.planSwitch.endsOn}{" "}
            {new Date(subscription.trialEndsAt).toLocaleDateString()}
          </span>
        )}
      </div>

      {subscription.scheduledPlan && subscription.scheduledChangeAt && (
        <div className="mt-4 p-3 rounded-xl bg-amber-500/10 border border-amber-500/20">
          <p className="text-sm text-amber-300">
            {t.planSwitch.scheduledSwitch}{" "}
            <span className="font-semibold">
              {PLAN_DETAILS[subscription.scheduledPlan]?.name ?? subscription.scheduledPlan}
            </span>{" "}
            {t.planSwitch.onDate}{" "}
            {new Date(subscription.scheduledChangeAt).toLocaleDateString()}
          </p>
          <Button
            variant="ghost"
            size="sm"
            onClick={onCancelScheduled}
            disabled={cancelingScheduled}
            className="mt-2 text-amber-400 hover:text-amber-300"
          >
            {cancelingScheduled
              ? t.planSwitch.canceling
              : t.planSwitch.cancelScheduledSwitch}
          </Button>
        </div>
      )}

      {!subscription.scheduledPlan && (
        <div className="mt-4 pt-4 border-t border-black/[0.08] flex flex-wrap gap-3">
          {targetPlans.map((tp) => (
            <Button
              key={tp.key}
              variant="secondary"
              size="md"
              onClick={() => onSwitchPlan(tp.key)}
            >
              {tp.action} {tp.label}
            </Button>
          ))}
        </div>
      )}
    </GlassCard>
  );
}
