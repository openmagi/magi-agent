"use client";

import { use } from "react";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

interface ChatIndexPageProps {
  params: Promise<{ botId: string }>;
}

export default function ChatIndexPage({ params }: ChatIndexPageProps) {
  const { botId } = use(params);
  const router = useRouter();
  useEffect(() => {
    router.replace(`/dashboard/${botId}/chat/default`);
  }, [router, botId]);
  return null;
}
