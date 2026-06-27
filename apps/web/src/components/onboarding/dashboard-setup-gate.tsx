"use client";

import { useEffect, useState } from "react";
import { loadLocalBootstrap } from "@/lib/local-auth";
import type { LocalBootstrapSetup } from "@/lib/local-auth";
import { OnboardingWizard } from "./onboarding-wizard";

/**
 * First-run mount gate for the OSS dashboard. Reads `/app/bootstrap.json` once
 * on mount and shows the onboarding wizard only when the backend signals
 * `setup.needed === true` (wizard flag ON and no provider configured). An absent
 * `setup` block (older backend) is treated as not-needed, so the dashboard
 * renders exactly as before.
 */
export function DashboardSetupGate(): React.ReactElement | null {
  const [setup, setSetup] = useState<LocalBootstrapSetup | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let active = true;
    void loadLocalBootstrap().then((bootstrap) => {
      if (!active) return;
      setSetup(bootstrap?.setup ?? null);
    });
    return () => {
      active = false;
    };
  }, []);

  const needed = setup?.needed === true;
  if (!needed || dismissed) return null;

  return (
    <OnboardingWizard
      open
      providers={setup?.providers ?? []}
      onClose={() => setDismissed(true)}
    />
  );
}
