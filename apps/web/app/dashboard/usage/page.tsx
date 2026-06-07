"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function UsageRedirectPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/dashboard/local/usage");
  }, [router]);

  return null;
}
