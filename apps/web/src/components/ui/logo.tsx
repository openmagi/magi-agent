const LOGO_ICON_SRC = "/openmagi-app-icon.png";
const LOGO_LIGHT_SRC = "/openmagi-logo-lockup.png";

export function LogoIcon({ className = "h-10 w-10" }: { className?: string }) {
  return (
    <img
      src={LOGO_ICON_SRC}
      alt="Open Magi"
      width={1024}
      height={1024}
      className={className}
    />
  );
}

export function Logo({ className = "" }: { className?: string }) {
  return (
    <img
      src={LOGO_LIGHT_SRC}
      alt="Open Magi"
      width={1945}
      height={470}
      className={`h-8 w-auto ${className}`}
    />
  );
}

export function LogoLarge({ className = "" }: { className?: string }) {
  return (
    <img
      src={LOGO_LIGHT_SRC}
      alt="Open Magi"
      width={1945}
      height={470}
      className={`h-14 w-auto ${className}`}
    />
  );
}

export function LogoHero({ className = "" }: { className?: string }) {
  return (
    <img
      src={LOGO_LIGHT_SRC}
      alt="Open Magi"
      width={1945}
      height={470}
      className={`h-20 w-auto ${className}`}
    />
  );
}
