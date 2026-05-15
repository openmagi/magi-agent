export const LEGACY_CLAWY_HOSTS = new Set(["clawy.pro", "www.clawy.pro"]);
export const OPEN_MAGI_ORIGIN = "https://openmagi.ai";

export type RedirectLocation = Pick<Location, "hash" | "hostname" | "pathname" | "search">;

export function isLegacyClawyHost(hostname: string): boolean {
  return LEGACY_CLAWY_HOSTS.has(hostname.trim().toLowerCase().replace(/\.$/, ""));
}

export function buildOpenMagiRedirectUrl(location: RedirectLocation): string {
  const target = new URL(OPEN_MAGI_ORIGIN);

  target.pathname = location.pathname || "/";
  target.search = location.search || "";
  target.hash = location.hash || "";

  return target.toString();
}
