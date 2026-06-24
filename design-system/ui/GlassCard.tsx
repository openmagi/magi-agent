import { cn } from "./cn"

interface GlassCardProps {
  children: React.ReactNode
  className?: string
  hover?: boolean
  glow?: boolean
  onClick?: () => void
}

/** Landing-oriented frosted card. Depends on the `.glass` / `.glow-sm`
 * utilities from the @ds:brand token extension, so it only renders fully on
 * surfaces that load the brand layer (clawy landing). */
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
      className={cn(
        "glass rounded-2xl p-5",
        hover &&
          "transition-all duration-200 hover:border-[var(--color-accent)]/20 cursor-pointer",
        glow && "glow-sm",
        className,
      )}
    >
      {children}
    </div>
  )
}
