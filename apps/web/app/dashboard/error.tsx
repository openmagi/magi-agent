"use client";

import { GlassCard } from "@/components/ui/glass-card";
import { Button } from "@/components/ui/button";

export default function DashboardError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="flex items-center justify-center min-h-[60vh]">
      <GlassCard className="max-w-md text-center">
        <h2 className="text-xl font-bold text-foreground mb-4">Something went wrong</h2>
        <p className="text-secondary mb-6">
          {error.message || "An unexpected error occurred."}
        </p>
        <Button variant="cta" size="md" onClick={reset}>
          Try again
        </Button>
      </GlassCard>
    </div>
  );
}
