"use client";

import { useEffect, useRef, useState } from "react";
import { buildOpenMagiRedirectUrl, isLegacyClawyHost } from "@/lib/legacy-domain";

export function LegacyDomainRebrandModal(): React.JSX.Element | null {
  const [redirectUrl, setRedirectUrl] = useState<string | null>(null);
  const ctaRef = useRef<HTMLAnchorElement>(null);

  useEffect(() => {
    if (!isLegacyClawyHost(window.location.hostname)) return;
    setRedirectUrl(buildOpenMagiRedirectUrl(window.location));
  }, []);

  useEffect(() => {
    if (!redirectUrl) return;
    ctaRef.current?.focus();
  }, [redirectUrl]);

  if (!redirectUrl) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-[#0b0f19]/55 p-4 backdrop-blur-sm">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="legacy-domain-rebrand-title"
        aria-describedby="legacy-domain-rebrand-description"
        className="w-full max-w-md rounded-xl border border-black/10 bg-white p-6 text-[#0b0f19] shadow-2xl"
      >
        <div className="flex items-center gap-3">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/openmagi-app-icon.png"
            alt=""
            aria-hidden="true"
            className="h-10 w-10 shrink-0 object-contain"
          />
          <div>
            <p className="text-xs font-extrabold uppercase tracking-[0.18em] text-[#E8735A]">
              New domain
            </p>
            <h2 id="legacy-domain-rebrand-title" className="mt-1 text-2xl font-black tracking-normal">
              Clawy has been rebranded to Open Magi.
            </h2>
          </div>
        </div>

        <p id="legacy-domain-rebrand-description" className="mt-5 text-base leading-7 text-[#475569]">
          You are visiting the old clawy.pro domain. Open Magi now runs at openmagi.ai.
          Your account and workspaces stay the same; continue there with this page preserved.
        </p>

        <a
          ref={ctaRef}
          href={redirectUrl}
          className="mt-6 flex min-h-12 w-full items-center justify-center rounded-lg bg-[#0b0f19] px-5 text-base font-extrabold text-white outline-none transition hover:bg-[#111827] focus-visible:ring-2 focus-visible:ring-[#E8735A] focus-visible:ring-offset-2"
        >
          Continue to Open Magi
        </a>

        <p className="mt-4 break-all text-center text-xs font-semibold text-[#64748b]">
          {redirectUrl}
        </p>
      </section>
    </div>
  );
}
