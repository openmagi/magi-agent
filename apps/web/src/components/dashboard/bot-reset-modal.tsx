"use client";

import { useState } from "react";
import { Modal } from "@/components/ui/modal";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
import { useMessages } from "@/lib/i18n";

interface BotResetModalProps {
  botId: string;
  open: boolean;
  onClose: () => void;
  onQueued: () => void;
}

const CONFIRMATION_PHRASE = "RESET";

export function BotResetModal({ botId, open, onClose, onQueued }: BotResetModalProps) {
  const authFetch = useAuthFetch();
  const t = useMessages();
  const [confirmation, setConfirmation] = useState("");
  const [queueing, setQueueing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isConfirmed = confirmation === CONFIRMATION_PHRASE;

  async function handleReset() {
    if (!isConfirmed) return;
    setQueueing(true);
    setError(null);

    try {
      const res = await authFetch(`/api/bots/${botId}/reset`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || t.errors.unexpected);
      }

      setConfirmation("");
      onQueued();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : t.errors.unexpected);
    } finally {
      setQueueing(false);
    }
  }

  function handleClose() {
    if (queueing) return;
    setConfirmation("");
    setError(null);
    onClose();
  }

  return (
    <Modal open={open} onClose={handleClose}>
      <div className="p-6 space-y-5">
        <div>
          <h2 className="text-lg font-semibold text-red-400">
            {t.settingsPage.resetBotTitle}
          </h2>
          <p className="text-sm text-secondary mt-2">
            {t.settingsPage.resetBotModalDescription}
          </p>
        </div>

        <div className="rounded-xl border border-red-500/20 bg-red-500/5 p-4 space-y-2 text-sm">
          <p className="text-red-400">{t.settingsPage.resetBotDeletes}</p>
          <p className="text-secondary">{t.settingsPage.resetBotPreserves}</p>
        </div>

        {error && (
          <div className="border border-red-500/20 text-red-400 px-4 py-3 rounded-xl text-sm">
            {error}
          </div>
        )}

        <Input
          label={t.settingsPage.resetBotTypeConfirmation}
          value={confirmation}
          onChange={(e) => setConfirmation(e.target.value)}
          placeholder={CONFIRMATION_PHRASE}
          disabled={queueing}
        />

        <div className="flex gap-3 justify-end">
          <Button variant="ghost" size="sm" onClick={handleClose} disabled={queueing}>
            {t.accountDeletion.cancel}
          </Button>
          <button
            type="button"
            onClick={handleReset}
            disabled={!isConfirmed || queueing}
            className="px-4 py-2 rounded-xl text-sm font-medium transition-all duration-200 bg-red-500/20 text-red-400 border border-red-500/30 hover:bg-red-500/30 hover:border-red-500/50 disabled:opacity-40 disabled:pointer-events-none cursor-pointer"
          >
            {queueing ? t.settingsPage.resetBotQueueing : t.settingsPage.resetBotConfirm}
          </button>
        </div>
      </div>
    </Modal>
  );
}
