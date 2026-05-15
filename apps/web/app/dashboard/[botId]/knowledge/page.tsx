"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

export default function BotKnowledgePage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/dashboard/knowledge");
  }, [router]);
  return null;
}
