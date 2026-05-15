"use client";

import { useState } from "react";
import { Modal } from "@/components/ui/modal";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
import { useMessages } from "@/lib/i18n";

interface BotDeleteModalProps {
  botId: string;
  open: boolean;
  onClose: () => void;
  onDeleted: () => void;
}

const CONFIRMATION_PHRASE = "DELETE";

export function BotDeleteModal({ botId, open, onClose, onDeleted }: BotDeleteModalProps) {
  const authFetch = useAuthFetch();
  const t = useMessages();
  const [confirmation, setConfirmation] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isConfirmed = confirmation === CONFIRMATION_PHRASE;

  async function handleDelete() {
    if (!isConfirmed) return;
    setDeleting(true);
    setError(null);

    try {
      const res = await authFetch(`/api/bots/${botId}`, { method: "DELETE" });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || t.errors.unexpected);
      }

      setConfirmation("");
      onDeleted();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : t.errors.unexpected);
    } finally {
      setDeleting(false);
    }
  }

  function handleClose() {
    if (deleting) return;
    setConfirmation("");
    setError(null);
    onClose();
  }

  return (
    <Modal open={open} onClose={handleClose}>
      <div className="p-6 space-y-5">
        <div>
          <h2 className="text-lg font-semibold text-red-400">
            {t.settingsPage.deleteBotTitle}
          </h2>
          <p className="text-sm text-secondary mt-2">
            {t.settingsPage.deleteBotModalDescription}
          </p>
        </div>

        <div className="rounded-xl border border-red-500/20 bg-red-500/5 p-4 space-y-2 text-sm">
          <p className="text-red-400">{t.settingsPage.deleteBotDeletes}</p>
          <p className="text-secondary">{t.settingsPage.deleteBotPreserves}</p>
        </div>

        {error && (
          <div className="border border-red-500/20 text-red-400 px-4 py-3 rounded-xl text-sm">
            {error}
          </div>
        )}

        <Input
          label={t.settingsPage.deleteBotTypeConfirmation}
          value={confirmation}
          onChange={(e) => setConfirmation(e.target.value)}
          placeholder={CONFIRMATION_PHRASE}
          disabled={deleting}
        />

        <div className="flex gap-3 justify-end">
          <Button variant="ghost" size="sm" onClick={handleClose} disabled={deleting}>
            {t.accountDeletion.cancel}
          </Button>
          <button
            type="button"
            onClick={handleDelete}
            disabled={!isConfirmed || deleting}
            className="px-4 py-2 rounded-xl text-sm font-medium transition-all duration-200 bg-red-500/20 text-red-400 border border-red-500/30 hover:bg-red-500/30 hover:border-red-500/50 disabled:opacity-40 disabled:pointer-events-none cursor-pointer"
          >
            {deleting ? t.settingsPage.deleteBotDeleting : t.settingsPage.deleteBotConfirm}
          </button>
        </div>
      </div>
    </Modal>
  );
}
