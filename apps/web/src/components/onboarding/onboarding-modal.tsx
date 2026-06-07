"use client";

import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";

interface OnboardingModalProps {
  open: boolean;
  onClose: () => void;
  sessionId?: string | null;
  onDeployComplete?: () => void;
  mode?: "add" | "create" | string;
  subscriptionPlan?: string;
}

export function OnboardingModal({
  open,
  onClose,
  sessionId: _sessionId,
  onDeployComplete: _onDeployComplete,
  mode: _mode,
  subscriptionPlan: _subscriptionPlan,
}: OnboardingModalProps) {
  return (
    <Modal open={open} onClose={onClose}>
      <div className="space-y-4 p-6">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Local agent</h2>
          <p className="mt-1 text-sm text-secondary">
            This OSS build runs against the local Magi Agent runtime. Cloud bot provisioning is not bundled.
          </p>
        </div>
        <Button variant="primary" size="md" onClick={onClose}>
          Close
        </Button>
      </div>
    </Modal>
  );
}
