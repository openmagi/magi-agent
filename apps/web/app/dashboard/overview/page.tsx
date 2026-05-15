"use client";

import { DashboardOverview } from "../overview-client";

export default function OverviewPage() {
  return (
    <DashboardOverview
      bot={null}
      sessionId={null}
      openOnboarding={false}
      subscriptionPlan="pro"
    />
  );
}
