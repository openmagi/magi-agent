// Local mode: no auth check needed, just render children
// generateStaticParams for static export - only "local" bot
export function generateStaticParams(): { botId: string }[] {
  return [{ botId: "local" }];
}

export default function BotLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
