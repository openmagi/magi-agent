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
        glass rounded-2xl p-6
        ${hover ? "transition-all duration-200 hover:bg-glass-hover hover:border-primary/20 cursor-pointer" : ""}
        ${glow ? "glow-sm" : ""}
        ${className}
      `}
    >
      {children}
    </div>
  );
}
