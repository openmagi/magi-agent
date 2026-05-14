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

const WORKSPACE_BOUNDARY_PATTERNS: Array<[RegExp, string]> = [
  [/\b(?:sudo|doas)\b|\bsu\s+-/, "privilege escalation command"],
  [/\bmkfs(?:\.[a-z0-9]+)?\b/, "filesystem formatting command"],
  [/\bdd\b[^|;&]*\bof=\s*\/dev\//, "raw disk write command"],
  [/:\s*\(\)\s*\{[^}]*:\s*\|\s*:\s*&?[^}]*\}/, "fork bomb"],
  [
    /\brm\s+(?:-[^\s]*r[^\s]*f|-[^\s]*f[^\s]*r)\s+(?:--\s+)?['"]?\/(?:\*|['"]?\s|$)/,
    "destructive rm -rf root",
  ],
  [/\b(?:chmod|chown)\s+-R\b[^|;&]*\s\/(?:\s|$)/, "recursive system permission change"],
  [/(^|[\s"'`=])\/(?:etc|sys|root)\b/, "system path access"],
  [/(^|[\s"'`=])\/(?:var\/run|run)\/secrets\b/, "runtime secret path access"],
  [/(^|[\s"'`=])\/proc\/(?:self|\d+)\/environ\b/, "process environment access"],
  [
    /(^|[\s"'`=])(?:~|\/home\/[^/\s"'`]+|\/Users\/[^/\s"'`]+|\/root)\/\.ssh\b/,
    "ssh secret path access",
  ],
  [
    /\b(?:curl|wget)\b[^\n]*(?:169\.254\.169\.254|metadata\.google\.internal|metadata\.azure\.com)/i,
    "cloud metadata access",
  ],
  [/\b(?:kubectl|helm)\b/, "cluster control command"],
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

export function classifyWorkspaceShellBoundary(command: string): ShellSafetyResult {
  const normalized = command.trim();
  for (const [pattern, reason] of WORKSPACE_BOUNDARY_PATTERNS) {
    if (pattern.test(normalized)) {
      return { safe: false, reason, complex: isComplexShell(normalized) };
    }
  }
  return { safe: true, complex: isComplexShell(normalized) };
}

function isComplexShell(command: string): boolean {
  return COMPLEX_PATTERNS.some((pattern) => pattern.test(command));
}
