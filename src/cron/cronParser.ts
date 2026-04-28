/**
 * Minimal 5-field cron parser for clawy-core-agent.
 *
 * Grammar:
 *   MINUTE HOUR DAY_OF_MONTH MONTH DAY_OF_WEEK
 *   each field supports: `*` | `N` | `a-b` | `a,b,c` | `*\/N` (step).
 *
 * Shorthands: `@hourly` `@daily`/`@midnight` `@weekly` `@monthly`
 * `@yearly`/`@annually`.
 *
 * POSIX oddity: when both day-of-month and day-of-week are restricted,
 * the cron fires on EITHER match (OR semantics). If only one is
 * restricted, that one is AND'd as usual.
 */

export interface CronFields {
  minute: Set<number>; // 0-59
  hour: Set<number>; // 0-23
  dayOfMonth: Set<number>; // 1-31
  month: Set<number>; // 1-12
  dayOfWeek: Set<number>; // 0-6 (0=Sun)
}

const FIELD_DEFS: Array<{ lo: number; hi: number; key: keyof CronFields }> = [
  { lo: 0, hi: 59, key: "minute" },
  { lo: 0, hi: 23, key: "hour" },
  { lo: 1, hi: 31, key: "dayOfMonth" },
  { lo: 1, hi: 12, key: "month" },
  { lo: 0, hi: 6, key: "dayOfWeek" },
];

const SHORTHANDS: Record<string, string> = {
  "@hourly": "0 * * * *",
  "@daily": "0 0 * * *",
  "@midnight": "0 0 * * *",
  "@weekly": "0 0 * * 0",
  "@monthly": "0 0 1 * *",
  "@yearly": "0 0 1 1 *",
  "@annually": "0 0 1 1 *",
};

export function parseCron(expression: string): CronFields {
  const raw = expression.trim().toLowerCase();
  const expr = SHORTHANDS[raw] ?? raw;
  const parts = expr.split(/\s+/);
  if (parts.length !== 5) {
    throw new Error(`invalid cron "${expression}": expected 5 fields, got ${parts.length}`);
  }
  const partial: Partial<CronFields> = {};
  for (let i = 0; i < 5; i++) {
    const def = FIELD_DEFS[i];
    if (!def) throw new Error("unreachable");
    partial[def.key] = parseField(parts[i] ?? "*", def.lo, def.hi);
  }
  return partial as CronFields;
}

function parseField(s: string, lo: number, hi: number): Set<number> {
  const out = new Set<number>();
  for (const piece of s.split(",")) {
    if (piece === "*") {
      for (let i = lo; i <= hi; i++) out.add(i);
    } else if (piece.startsWith("*/")) {
      const step = parseInt(piece.slice(2), 10);
      if (!(step > 0)) throw new Error(`bad step "${piece}"`);
      for (let i = lo; i <= hi; i += step) out.add(i);
    } else if (piece.includes("-")) {
      const [aStr, bStr] = piece.split("-", 2);
      const a = parseInt(aStr ?? "", 10);
      const b = parseInt(bStr ?? "", 10);
      if (Number.isNaN(a) || Number.isNaN(b) || a > b || a < lo || b > hi) {
        throw new Error(`bad range "${piece}" in ${lo}-${hi}`);
      }
      for (let i = a; i <= b; i++) out.add(i);
    } else {
      const n = parseInt(piece, 10);
      if (Number.isNaN(n) || n < lo || n > hi) {
        throw new Error(`bad value "${piece}" outside ${lo}-${hi}`);
      }
      out.add(n);
    }
  }
  return out;
}

/**
 * Returns the next Date (in local time of `after`) at which the cron
 * expression fires, strictly after `after`. Iterates minute-by-minute
 * with an upper bound of 4 years — any valid expression resolves well
 * before that.
 */
export function getNextFireAt(expression: string, after: Date): Date {
  const fields = parseCron(expression);
  const d = new Date(after.getTime() + 60_000); // start strictly after
  d.setSeconds(0, 0);
  const maxSteps = 525_600 * 4;
  for (let i = 0; i < maxSteps; i++) {
    if (matches(d, fields)) return new Date(d);
    d.setMinutes(d.getMinutes() + 1);
  }
  throw new Error(`could not find next fire time for "${expression}"`);
}

function matches(d: Date, f: CronFields): boolean {
  if (!f.minute.has(d.getMinutes())) return false;
  if (!f.hour.has(d.getHours())) return false;
  if (!f.month.has(d.getMonth() + 1)) return false;

  const domRestricted = f.dayOfMonth.size !== 31;
  const dowRestricted = f.dayOfWeek.size !== 7;
  const domOk = f.dayOfMonth.has(d.getDate());
  const dowOk = f.dayOfWeek.has(d.getDay());
  if (domRestricted && dowRestricted) {
    return domOk || dowOk; // POSIX OR semantics
  }
  if (domRestricted) return domOk;
  if (dowRestricted) return dowOk;
  return true;
}
