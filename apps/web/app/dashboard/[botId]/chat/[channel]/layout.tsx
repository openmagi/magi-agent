export function generateStaticParams() {
  return [{ channel: "default" }];
}

export default function ChannelLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
