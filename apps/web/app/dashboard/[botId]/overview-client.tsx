"use client";

import { useState, useCallback, useEffect } from "react";
import dynamic from "next/dynamic";
import { useRouter, useParams } from "next/navigation";
import { BotStatusCard } from "@/components/dashboard/bot-status-card";
import { IntegrationsPanel } from "@/components/dashboard/integrations-panel";
import { GlassCard } from "@/components/ui/glass-card";
import { Button } from "@/components/ui/button";
import { useMessages } from "@/lib/i18n";

const OnboardingModal = dynamic(
  () => import("@/components/onboarding/onboarding-modal").then((m) => m.OnboardingModal),
  { ssr: false }
);
import type { BotCardData } from "@/types/entities";

interface DashboardOverviewProps {
  bot: BotCardData | null;
  sessionId?: string | null;
  openOnboarding?: boolean;
  subscriptionPlan?: string;
}

export function DashboardOverview({ bot, sessionId, openOnboarding, subscriptionPlan }: DashboardOverviewProps) {
  const t = useMessages();
  const router = useRouter();
  const params = useParams();
  const botId = params.botId as string | undefined;
  const [modalOpen, setModalOpen] = useState(openOnboarding ?? false);

  const overviewPath = botId ? `/dashboard/${botId}/overview` : "/dashboard";

  const handleDeployComplete = useCallback((newBotId?: string) => {
    setModalOpen(false);
    if (newBotId) {
      router.push(`/dashboard/${newBotId}/overview`);
    } else {
      router.refresh();
      router.replace(overviewPath, { scroll: false });
    }
  }, [router, overviewPath]);

  const handleClose = useCallback(() => {
    setModalOpen(false);
    router.replace(overviewPath, { scroll: false });
  }, [router, overviewPath]);

  // Listen for "add bot" events from sidebar (same page) or sessionStorage flag (cross-page)
  useEffect(() => {
    const handler = () => setModalOpen(true);
    window.addEventListener("clawy:open-add-bot", handler);

    // Check sessionStorage flag (set by sidebar when navigating from another page)
    const flag = sessionStorage.getItem("clawy:open-add-bot");
    if (flag) {
      sessionStorage.removeItem("clawy:open-add-bot");
      setModalOpen(true);
    }

    return () => window.removeEventListener("clawy:open-add-bot", handler);
  }, []);

  return (
    <div className="max-w-2xl">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-foreground">{t.dashboard.title}</h1>
        <p className="text-secondary text-sm mt-1">
          {bot ? t.dashboard.subtitleWithBot : t.dashboard.subtitleNoBot}
        </p>
      </div>

      {bot ? (
        <div className="space-y-6">
          <BotStatusCard bot={bot} subscriptionPlan={subscriptionPlan} />
          <IntegrationsPanel botId={bot.id} />
        </div>
      ) : (
        <GlassCard className="text-center py-12">
          <div className="flex flex-col items-center gap-4">
            <div className="w-14 h-14 rounded-full bg-gradient-to-br from-primary/20 to-cta/20 flex items-center justify-center">
              <svg viewBox="0 0 24 24" fill="none" className="w-7 h-7 text-primary-light" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 5v14M5 12h14" />
              </svg>
            </div>
            <div>
              <p className="text-foreground font-medium mb-1">{t.dashboard.noBotTitle}</p>
              <p className="text-secondary text-sm">{t.dashboard.noBotDescription}</p>
            </div>
            <Button variant="cta" size="md" onClick={() => setModalOpen(true)}>
              {t.dashboard.deployFirstBot}
            </Button>
          </div>
        </GlassCard>
      )}

      <OnboardingModal
        open={modalOpen}
        onClose={handleClose}
        sessionId={sessionId}
        onDeployComplete={handleDeployComplete}
        mode={bot ? "add" : "create"}
        subscriptionPlan={subscriptionPlan}
      />
    </div>
  );
}
