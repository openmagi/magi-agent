export interface ShellSafetyResult {
  safe: boolean;
  reason?: string;
  complex: boolean;
}

const DESTRUCTIVE_PATTERNS: Array<[RegExp, string]> = [
  [/\brm\s+(?:-[^\s]*r[^\s]*f|-[^\s]*f[^\s]*r)\b/, "destructive rm -rf"],
  [/\bchmod\s+-R\s+777\b/, "unsafe recursive chmod"],
  [/\bmkfs(?:\.[a-z0-9]+)?\b/, "filesystem formatting command"],
  [/\bdd\b[^|;&]*\bof=/, "raw disk write command"],
  [/:\s*\(\)\s*\{\s*:\s*\|\s*:\s*;\s*\}/, "fork bomb"],
  [/\bfind\s+\.\.\s+.*-name\s+['"]?\.env['"]?.*-exec\s+cat\b/, "recursive secret read"],
  [/\btar\b.*(?:~\/\.ssh|\.ssh).*[\|;&&]+\s*\bcurl\b/, "archive secret exfiltration"],
];

const SECRET_EXFIL_PATTERNS: Array<[RegExp, string]> = [
  [/\benv\b.*\|\s*grep\s+.*(?:TOKEN|SECRET|KEY)/i, "environment secret exfiltration"],
  [/\b(?:curl|wget)\b.*\$(?:[A-Z0-9_]*TOKEN|[A-Z0-9_]*SECRET|[A-Z0-9_]*KEY)\b/i, "network secret exfiltration"],
  [/(?:>|>>)\s*(?:\.env|[^ ]*\/\.env)(?:\s|$)/, "redirection to secret path"],
  [/\bcat\b\s+(?:\.env|~\/\.ssh|[^ ]*\/\.env)\b/, "secret file read"],
];

const COMPLEX_PATTERNS: RegExp[] = [
  /[`$]\(/,
  /[`]/,
  /(?:^|[^&])&&(?:[^&]|$)|\|\||;/,
  /\b(?:python|python3)\s+-c\b/,
  /\b(?:node|perl|ruby)\s+-e\b/,
];

export function classifyShellSafety(command: string): ShellSafetyResult {
  const normalized = command.trim();
  for (const [pattern, reason] of [...DESTRUCTIVE_PATTERNS, ...SECRET_EXFIL_PATTERNS]) {
    if (pattern.test(normalized)) {
      return { safe: false, reason, complex: isComplexShell(normalized) };
    }
  }
  const complex = isComplexShell(normalized);
  return {
    safe: !complex,
    complex,
    ...(complex ? { reason: "complex shell requires explicit approval" } : {}),
  };
}

function isComplexShell(command: string): boolean {
  return COMPLEX_PATTERNS.some((pattern) => pattern.test(command));
}
