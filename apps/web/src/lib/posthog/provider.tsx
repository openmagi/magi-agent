"use client";

import posthog from "posthog-js";
import { PostHogProvider as PHProvider } from "posthog-js/react";
import { useEffect } from "react";
import { usePrivy } from "@privy-io/react-auth";
import { getCookieConsent } from "@/lib/consent/store";

const POSTHOG_KEY = process.env.NEXT_PUBLIC_POSTHOG_KEY;
const POSTHOG_HOST = process.env.NEXT_PUBLIC_POSTHOG_HOST || "https://us.i.posthog.com";

if (typeof window !== "undefined" && POSTHOG_KEY) {
  posthog.init(POSTHOG_KEY, {
    api_host: POSTHOG_HOST,
    person_profiles: "identified_only",
    capture_pageview: false, // Manual SPA tracking via PostHogPageView
    opt_out_capturing_by_default: true,
  });

  // Restore consent from previous session
  if (getCookieConsent() === "accepted") {
    posthog.opt_in_capturing();
  }
}

function PostHogIdentifier(): null {
  const { user, authenticated } = usePrivy();

  useEffect(() => {
    if (authenticated && user?.id) {
      posthog.identify(user.id);
    } else if (!authenticated) {
      posthog.reset();
    }
  }, [authenticated, user?.id]);

  return null;
}

export function PostHogProvider({ children }: { children: React.ReactNode }) {
  if (!POSTHOG_KEY) {
    return <>{children}</>;
  }

  return (
    <PHProvider client={posthog}>
      <PostHogIdentifier />
      {children}
    </PHProvider>
  );
}
