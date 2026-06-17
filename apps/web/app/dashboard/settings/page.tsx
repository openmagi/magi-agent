"use client";

import { SettingsForm } from "@/components/dashboard/settings-form";
import { ProviderKeysForm } from "@/components/dashboard/provider-keys-form";

export default function SettingsPage() {
  return (
    <div className="space-y-8">
      <SettingsForm bot={null} />
      <section>
        <h2 className="mb-4 text-xl font-semibold text-foreground">Models &amp; Keys</h2>
        <ProviderKeysForm bot={null} />
      </section>
    </div>
  );
}
