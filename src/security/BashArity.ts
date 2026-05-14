/**
 * BashArity — semantic command prefix extraction via arity dictionary.
 *
 * Port of OpenCode's arity model: maps command prefixes to the number
 * of tokens that form the "semantic command." For example, `git` has
 * arity 2, so `git push origin main` yields semantic prefix `git push`.
 *
 * Uses longest-match-first so `npm run` (arity 3) beats `npm` (arity 2)
 * for `npm run dev`.
 */

const ARITY: Record<string, number> = {
  git: 2,
  npm: 2,
  "npm run": 3,
  npx: 2,
  yarn: 2,
  "yarn run": 3,
  pnpm: 2,
  "pnpm run": 3,
  bun: 2,
  "bun run": 3,
  docker: 2,
  "docker compose": 3,
  "docker buildx": 3,
  kubectl: 2,
  helm: 2,
  sudo: 2,
  nerdctl: 2,
  skopeo: 2,
  ctr: 2,
  magi: 2,
  pip: 2,
  pip3: 2,
  cargo: 2,
  go: 2,
  make: 1,
  systemctl: 2,
  journalctl: 1,
  ssh: 1,
  scp: 1,
  rsync: 1,
  curl: 1,
  wget: 1,
  rm: 1,
  chmod: 1,
  chown: 1,
  mv: 1,
  cp: 1,
  ln: 1,
  tar: 1,
  unzip: 1,
  python: 1,
  python3: 1,
  node: 1,
  deno: 2,
};

/**
 * Sorted ARITY keys by descending token count so longest prefix matches
 * first. Computed once at module load.
 */
const SORTED_KEYS: Array<{ key: string; tokens: string[]; arity: number }> = Object.entries(ARITY)
  .map(([key, arity]) => ({ key, tokens: key.split(" "), arity }))
  .sort((a, b) => b.tokens.length - a.tokens.length);

/**
 * Shell-aware tokenizer: splits on unquoted whitespace, handles single
 * and double quotes and backslash escapes.
 */
export function tokenize(segment: string): string[] {
  const tokens: string[] = [];
  let current = "";
  let inSingle = false;
  let inDouble = false;
  let escaped = false;

  for (let i = 0; i < segment.length; i++) {
    const ch = segment[i];

    if (escaped) {
      current += ch;
      escaped = false;
      continue;
    }

    if (ch === "\\") {
      escaped = true;
      continue;
    }

    if (ch === "'" && !inDouble) {
      inSingle = !inSingle;
      continue;
    }

    if (ch === '"' && !inSingle) {
      inDouble = !inDouble;
      continue;
    }

    if ((ch === " " || ch === "\t") && !inSingle && !inDouble) {
      if (current) {
        tokens.push(current);
        current = "";
      }
      continue;
    }

    current += ch;
  }

  if (current) tokens.push(current);
  return tokens;
}

export interface CommandSegment {
  command: string;
  args: string[];
  raw: string;
}

type Connector = "|" | "&&" | "||" | ";";
const CONNECTORS: readonly Connector[] = ["||", "&&", "|", ";"];

/**
 * Split a shell command into segments separated by connectors (|, &&, ||, ;).
 * Respects single/double quotes.
 */
export function splitSegments(command: string): CommandSegment[] {
  const trimmed = command.trim();
  if (!trimmed) return [];

  const rawParts: string[] = [];
  let current = "";
  let inSingle = false;
  let inDouble = false;
  let escaped = false;

  let i = 0;
  while (i < trimmed.length) {
    const ch = trimmed[i];

    if (escaped) {
      current += ch;
      escaped = false;
      i++;
      continue;
    }

    if (ch === "\\") {
      escaped = true;
      current += ch;
      i++;
      continue;
    }

    if (ch === "'" && !inDouble) {
      inSingle = !inSingle;
      current += ch;
      i++;
      continue;
    }

    if (ch === '"' && !inSingle) {
      inDouble = !inDouble;
      current += ch;
      i++;
      continue;
    }

    if (inSingle || inDouble) {
      current += ch;
      i++;
      continue;
    }

    let matched: Connector | null = null;
    for (const conn of CONNECTORS) {
      if (trimmed.startsWith(conn, i)) {
        matched = conn;
        break;
      }
    }

    if (matched) {
      rawParts.push(current);
      current = "";
      i += matched.length;
      continue;
    }

    current += ch;
    i++;
  }

  if (current.trim()) {
    rawParts.push(current);
  }

  return rawParts
    .map((raw) => {
      const tokens = tokenize(raw.trim());
      if (tokens.length === 0) return null;
      const cmd = tokens[0];
      if (!cmd) return null;
      return {
        command: cmd,
        args: tokens.slice(1),
        raw: raw.trim(),
      };
    })
    .filter((s): s is NonNullable<typeof s> => s !== null);
}

/**
 * Given a tokenized command, return the semantic prefix tokens.
 * Longest-match-first against ARITY keys; fallback arity 1.
 */
export function prefix(tokens: string[]): string[] {
  if (tokens.length === 0) return [];

  for (const entry of SORTED_KEYS) {
    if (entry.tokens.length > tokens.length) continue;
    let match = true;
    for (let i = 0; i < entry.tokens.length; i++) {
      if (tokens[i] !== entry.tokens[i]) {
        match = false;
        break;
      }
    }
    if (match) {
      return tokens.slice(0, entry.arity);
    }
  }

  return tokens.slice(0, 1);
}

/**
 * Compute the semantic prefix string from a raw shell command.
 * Returns the prefix of the first segment only.
 */
export function semanticPrefix(command: string): string {
  const segments = splitSegments(command);
  if (segments.length === 0) return "";
  const first = segments[0];
  if (!first) return "";
  const tokens = [first.command, ...first.args];
  return prefix(tokens).join(" ");
}

/**
 * Build the semantic pattern string for a segment.
 * E.g. `git push origin main` -> `git push *`
 */
export function semanticPattern(segment: CommandSegment): string {
  const tokens = [segment.command, ...segment.args];
  const p = prefix(tokens);
  return p.join(" ") + " *";
}
