// Frosted card using the `.glass` / `.glow-sm` utilities from the canonical
// @ds:brand token extension. Re-tokened to canonical accent.
interface GlassCardProps {
  children: React.ReactNode;
  className?: string;
  hover?: boolean;
  glow?: boolean;
  onClick?: () => void;
}

export function GlassCard({
  children,
  className = "",
  hover = false,
  glow = false,
  onClick,
}: GlassCardProps) {
  return (
    <div
      onClick={onClick}
      className={`
        glass rounded-2xl p-5
        ${hover ? "transition-all duration-200 hover:border-[var(--color-accent)]/20 cursor-pointer" : ""}
        ${glow ? "glow-sm" : ""}
        ${className}
      `}
    >
      {children}
    </div>
  );
}
