/** className composer — dependency-free clsx-compatible subset.
 *
 * Handles the patterns the design-system primitives use: strings, falsy
 * values (skipped), nested arrays, and `{ "class": boolean }` objects. Kept
 * zero-dependency on purpose so this whole `ui/` bundle vendors cleanly into
 * every consumer repo with no external imports. */
export type ClassValue =
  | string
  | number
  | null
  | undefined
  | false
  | ClassValue[]
  | { [key: string]: boolean | null | undefined };

export function cn(...inputs: ClassValue[]): string {
  const out: string[] = [];
  for (const input of inputs) {
    if (!input) continue;
    if (typeof input === "string" || typeof input === "number") {
      out.push(String(input));
    } else if (Array.isArray(input)) {
      const inner = cn(...input);
      if (inner) out.push(inner);
    } else if (typeof input === "object") {
      for (const key in input) {
        if (input[key]) out.push(key);
      }
    }
  }
  return out.join(" ");
}
