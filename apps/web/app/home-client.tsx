"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * OSS home page — redirects straight to the local dashboard.
 */
export function HomeClient() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/dashboard/local/chat");
  }, [router]);

  return (
    <div className="flex items-center justify-center min-h-screen">
      <p className="text-secondary text-sm">Redirecting to dashboard...</p>
    </div>
  );
}
