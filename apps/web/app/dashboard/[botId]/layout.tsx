export function generateStaticParams() {
  return [{ botId: "local" }];
}

export default function BotLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
