"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

export default function ChatIndexPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/dashboard/chat/default");
  }, [router]);
  return null;
}
