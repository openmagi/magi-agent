"use client";

/**
 * Customize page — full-page hub (Phase 4).
 *
 * Mounts the new ``CustomizeHub`` (sub-nav + page-resident panels) instead of
 * the legacy ``CustomizeRuntimeConsole`` modal duo. The sub-nav is mirrored in
 * the URL as ``?section=verification|tools|recipes|hooks`` so a deep-link lands
 * the user on the correct sub-page.
 *
 * The legacy ``CustomizeRuntimeConsole`` component is preserved (still exported
 * from ``customize-tab.tsx``) so tests and any embedded usages keep working;
 * only the route mount changes.
 */

import { useCallback } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import {
  CustomizeHub,
  type CustomizeSection,
} from "@/components/dashboard/customize/customize-hub";

const VALID_SECTIONS: ReadonlyArray<CustomizeSection> = [
  "verification",
  "tools",
  "recipes",
  "hooks",
];

function isValidSection(value: string | null): value is CustomizeSection {
  return value !== null && (VALID_SECTIONS as ReadonlyArray<string>).includes(value);
}

export default function CustomizePage() {
  const params = useParams<{ botId?: string | string[] }>();
  const search = useSearchParams();
  const router = useRouter();
  const rawBotId = params?.botId;
  const botId = Array.isArray(rawBotId) ? rawBotId[0] : rawBotId ?? "local";

  const raw = search?.get("section") ?? null;
  const initial: CustomizeSection = isValidSection(raw) ? raw : "verification";

  const handleSectionChange = useCallback(
    (next: CustomizeSection) => {
      const qs = new URLSearchParams(search?.toString() ?? "");
      qs.set("section", next);
      router.replace(`?${qs.toString()}`, { scroll: false });
    },
    [router, search],
  );

  return (
    <CustomizeHub
      botId={botId}
      initialSection={initial}
      onSectionChange={handleSectionChange}
    />
  );
}
