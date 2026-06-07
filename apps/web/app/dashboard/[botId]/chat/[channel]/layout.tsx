export function generateStaticParams() {
  return [{ channel: "general" }];
}

export default function ChannelLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
