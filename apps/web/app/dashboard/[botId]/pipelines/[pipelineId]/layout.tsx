export function generateStaticParams() {
  return [{ pipelineId: "default" }];
}

export default function PipelineLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
