import type { ButtonHTMLAttributes } from "react";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "ghost" | "cta";
  size?: "sm" | "md" | "lg";
}

const variants = {
  primary:
    "bg-primary text-white hover:bg-primary-light glow-sm hover:glow transition-all duration-200",
  secondary:
    "bg-transparent border border-black/10 text-foreground hover:border-primary/40 hover:bg-black/[0.04] transition-all duration-200",
  ghost:
    "bg-transparent text-secondary hover:text-foreground hover:bg-black/[0.04] transition-all duration-200",
  cta:
    "bg-cta text-white hover:bg-cta-light glow-cta transition-all duration-200",
};

const sizes = {
  sm: "px-4 py-2 text-sm rounded-lg gap-1.5 min-h-[44px]",
  md: "px-5 py-2.5 text-sm rounded-xl gap-2 min-h-[44px]",
  lg: "px-7 py-3.5 text-base rounded-xl gap-2 min-h-[44px]",
};

export function Button({
  variant = "primary",
  size = "md",
  className = "",
  disabled,
  ...props
}: ButtonProps) {
  return (
    <button
      className={`
        inline-flex items-center justify-center font-semibold cursor-pointer
        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/45
        focus-visible:ring-offset-2 focus-visible:ring-offset-background
        disabled:opacity-40 disabled:pointer-events-none
        ${variants[variant]}
        ${sizes[size]}
        ${className}
      `}
      disabled={disabled}
      {...props}
    />
  );
}
