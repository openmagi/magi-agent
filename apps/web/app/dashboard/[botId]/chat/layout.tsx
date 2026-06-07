/**
 * Chat layout: full-screen overlay that escapes the parent dashboard padding.
 * Uses fixed positioning with explicit top-0 to cover the entire viewport,
 * including over the mobile sticky header from the dashboard layout.
 */
export default function ChatLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-40 bg-background overflow-hidden">
      {children}
    </div>
  );
}
