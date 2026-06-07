"use client";

import { Button } from "@/components/ui/button";

interface StepTelegramProps {
  onConnect?: (token: string) => void | Promise<void>;
}

export function StepTelegram({ onConnect }: StepTelegramProps) {
  return (
    <div className="rounded-xl border border-black/10 bg-black/[0.02] p-4">
      <p className="text-sm font-medium text-foreground">Telegram connection</p>
      <p className="mt-1 text-xs text-secondary">
        Telegram auto-provisioning is not included in the OSS dashboard.
      </p>
      {onConnect && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="mt-3"
          onClick={() => void onConnect("")}
        >
          Continue
        </Button>
      )}
    </div>
  );
}
