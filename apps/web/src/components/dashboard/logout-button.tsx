"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { usePrivy } from "@privy-io/react-auth";
import { Button } from "@/components/ui/button";
import { useMessages } from "@/lib/i18n";

export function LogoutButton() {
  const { logout } = usePrivy();
  const router = useRouter();
  const t = useMessages();
  const [loggingOut, setLoggingOut] = useState(false);

  async function handleLogout() {
    setLoggingOut(true);
    try {
      await logout();
      router.push("/");
    } catch {
      setLoggingOut(false);
    }
  }

  return (
    <Button
      variant="ghost"
      size="sm"
      className="w-full justify-start text-secondary"
      onClick={handleLogout}
      disabled={loggingOut}
    >
      {loggingOut ? "..." : t.dashboard.logOut}
    </Button>
  );
}
