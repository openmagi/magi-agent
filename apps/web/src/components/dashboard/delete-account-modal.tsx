"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { usePrivy } from "@privy-io/react-auth";
import { Modal } from "@/components/ui/modal";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
import { useMessages } from "@/lib/i18n";

interface DeleteAccountModalProps {
  open: boolean;
  onClose: () => void;
}

const CONFIRMATION_PHRASE = "Delete this account";

export function DeleteAccountModal({ open, onClose }: DeleteAccountModalProps) {
  const authFetch = useAuthFetch();
  const { logout } = usePrivy();
  const router = useRouter();
  const t = useMessages();
  const [step, setStep] = useState<1 | 2>(1);
  const [confirmation, setConfirmation] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isConfirmed = confirmation === CONFIRMATION_PHRASE;

  async function handleDelete() {
    if (!isConfirmed) return;
    setDeleting(true);
    setError(null);

    try {
      const res = await authFetch("/api/account", { method: "DELETE" });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || t.errors.unexpected);
      }

      await logout();
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : t.errors.unexpected);
      setDeleting(false);
    }
  }

  function handleClose() {
    if (deleting) return;
    setStep(1);
    setConfirmation("");
    setError(null);
    onClose();
  }

  const warningIcon = (
    <svg className="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.999L13.732 4.001c-.77-1.333-2.694-1.333-3.464 0L3.34 16.001c-.77 1.332.192 2.999 1.732 2.999z" />
    </svg>
  );

  return (
    <Modal open={open} onClose={handleClose}>
      <div className="p-6 space-y-5">
        {step === 1 ? (
          <>
            <div>
              <h2 className="text-lg font-semibold text-red-400">
                {t.accountDeletion.modalTitle}
              </h2>
              <p className="text-sm text-secondary mt-2">
                {t.accountDeletion.modalDescription}
              </p>
            </div>

            <div className="glass border border-red-500/20 rounded-xl p-4 space-y-3">
              <p className="text-sm font-medium text-red-400">
                {t.accountDeletion.warningTitle}
              </p>
              <div className="space-y-2 text-sm text-red-300/80">
                <div className="flex items-center gap-2">
                  {warningIcon}
                  <span>{t.accountDeletion.warningBots}</span>
                </div>
                <div className="flex items-center gap-2">
                  {warningIcon}
                  <span>{t.accountDeletion.warningSubscription}</span>
                </div>
                <div className="flex items-center gap-2">
                  {warningIcon}
                  <span>{t.accountDeletion.warningData}</span>
                </div>
              </div>
            </div>

            <div className="flex gap-3 justify-end">
              <Button variant="ghost" size="sm" onClick={handleClose}>
                {t.accountDeletion.cancel}
              </Button>
              <button
                onClick={() => setStep(2)}
                className="px-4 py-2 rounded-xl text-sm font-medium transition-all duration-200 bg-red-500/20 text-red-400 border border-red-500/30 hover:bg-red-500/30 hover:border-red-500/50 cursor-pointer"
              >
                {t.accountDeletion.continueButton}
              </button>
            </div>
          </>
        ) : (
          <>
            <div>
              <h2 className="text-lg font-semibold text-red-400">
                {t.accountDeletion.finalConfirmTitle}
              </h2>
              <p className="text-sm text-secondary mt-2">
                {t.accountDeletion.finalConfirmDescription}
              </p>
            </div>

            {error && (
              <div className="glass border border-red-500/20 text-red-400 px-4 py-3 rounded-xl text-sm">
                {error}
              </div>
            )}

            <div>
              <Input
                label={t.accountDeletion.typeConfirmation}
                value={confirmation}
                onChange={(e) => setConfirmation(e.target.value)}
                placeholder={CONFIRMATION_PHRASE}
                disabled={deleting}
              />
            </div>

            <div className="flex gap-3 justify-end">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => { setStep(1); setConfirmation(""); setError(null); }}
                disabled={deleting}
              >
                {t.accountDeletion.back}
              </Button>
              <button
                onClick={handleDelete}
                disabled={!isConfirmed || deleting}
                className="px-4 py-2 rounded-xl text-sm font-medium transition-all duration-200 bg-red-500/20 text-red-400 border border-red-500/30 hover:bg-red-500/30 hover:border-red-500/50 disabled:opacity-40 disabled:pointer-events-none cursor-pointer"
              >
                {deleting ? t.accountDeletion.deleting : t.accountDeletion.deleteButton}
              </button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
