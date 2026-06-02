from __future__ import annotations

import re


MAX_TOOL_PREVIEW = 400

_BEARER_TOKEN_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_AUTHORIZATION_HEADER_RE = re.compile(
    r"\b((?:Proxy-)?Authorization\s*:\s*[A-Za-z][A-Za-z0-9+.-]*\s+)"
    r"([^\s,;]+)",
    re.IGNORECASE,
)
_COOKIE_HEADER_RE = re.compile(
    r"\b((?:Set-)?Cookie\s*:\s*)"
    r"(.+?)(?=(?:\s+and\s+|\s*,|\n|$|"
    r"\s+(?:(?:Proxy-)?Authorization|(?:Set-)?Cookie|credentials?)\s*[:=]))",
    re.IGNORECASE,
)
_GITHUB_TOKEN_RE = re.compile(r"\bgh[opusr]_[A-Za-z0-9_]+\b")
_OPENAI_TOKEN_RE = re.compile(r"\bsk-[A-Za-z0-9._-]+\b")
_STRIPE_TOKEN_RE = re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9_]+\b")
_SECRET_KEY_NAME = (
    r"(?:"
    r"[A-Za-z0-9_-]*(?:api[_-]?key|secret[_-]?key|service[_-]?role[_-]?key)"
    r"[A-Za-z0-9_-]*"
    r"|"
    r"[A-Za-z0-9_-]*(?:access|auth|bearer|id|refresh|session)[_-]?token"
    r"[A-Za-z0-9_-]*"
    r"|"
    r"[A-Za-z0-9_-]*(?:session[_-]?(?:key|id)|session(?:key|id))"
    r"[A-Za-z0-9_-]*"
    r"|"
    r"[A-Za-z0-9_-]*(?:token|secret|password|passphrase|private[_-]?key|client[_-]?secret)"
    r"[A-Za-z0-9_-]*"
    r")"
)
_PUBLIC_CREDENTIAL_KEY_NAME = (
    r"(?:"
    r"proxy_authorization|proxyAuthorization|ProxyAuthorization|proxyauthorization"
    r"|"
    r"authorization|Authorization"
    r"|"
    r"set_cookie|setCookie|SetCookie|setcookie|Setcookie"
    r"|"
    r"cookie|Cookie"
    r"|"
    r"credentials?"
    r"|"
    r"Credentials?"
    r")"
)
_DOUBLE_QUOTED_PUBLIC_CREDENTIAL_KEY_VALUE_RE = re.compile(
    rf"(?<![A-Za-z0-9_-])([\"']?{_PUBLIC_CREDENTIAL_KEY_NAME}[\"']?\s*[:=]\s*)"
    r'"((?:\\.|[^"\\])*)"'
)
_SINGLE_QUOTED_PUBLIC_CREDENTIAL_KEY_VALUE_RE = re.compile(
    rf"(?<![A-Za-z0-9_-])([\"']?{_PUBLIC_CREDENTIAL_KEY_NAME}[\"']?\s*[:=]\s*)"
    r"'((?:\\.|[^'\\])*)'"
)
_UNQUOTED_PUBLIC_CREDENTIAL_KEY_VALUE_RE = re.compile(
    rf"(?<![A-Za-z0-9_-])([\"']?{_PUBLIC_CREDENTIAL_KEY_NAME}[\"']?\s*[:=]\s*)"
    r"(?![A-Za-z][A-Za-z0-9+.-]*\s+\[redacted\])"
    r"("
    r"[A-Za-z][A-Za-z0-9+.-]*\s+[A-Za-z0-9._~+/=-]+"
    r"|"
    r"[^\"'\s,}\n](?:(?!\s+[A-Za-z0-9_-]+\s*[:=])[^\"',}\n])*"
    r")"
)
_DOUBLE_QUOTED_KEY_VALUE_SECRET_RE = re.compile(
    r"(?i)"
    rf"([\"']?{_SECRET_KEY_NAME}[\"']?\s*[:=]\s*)"
    r'"((?:\\.|[^"\\])*)"'
)
_SINGLE_QUOTED_KEY_VALUE_SECRET_RE = re.compile(
    r"(?i)"
    rf"([\"']?{_SECRET_KEY_NAME}[\"']?\s*[:=]\s*)"
    r"'((?:\\.|[^'\\])*)'"
)
_UNQUOTED_KEY_VALUE_SECRET_RE = re.compile(
    r"(?i)"
    rf"([\"']?{_SECRET_KEY_NAME}[\"']?\s*[:=]\s*)"
    r"([^\"'\s,}\n](?:(?!\s+[A-Za-z0-9_-]+\s*[:=])[^\"',}\n])*)"
)
_SESSION_ASSIGNMENT_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])([\"']?session[\"']?\s*=\s*)"
    r"([^\"'\s,}\n](?:(?!\s+[A-Za-z0-9_-]+\s*[:=])[^\"',}\n])*)"
)


def sanitize_tool_preview(preview: str) -> str:
    redacted = _BEARER_TOKEN_RE.sub(r"\1[redacted]", preview)
    redacted = _AUTHORIZATION_HEADER_RE.sub(r"\1[redacted]", redacted)
    redacted = _COOKIE_HEADER_RE.sub(r"\1[redacted]", redacted)
    redacted = _GITHUB_TOKEN_RE.sub("[redacted]", redacted)
    redacted = _OPENAI_TOKEN_RE.sub("[redacted]", redacted)
    redacted = _STRIPE_TOKEN_RE.sub("[redacted]", redacted)
    redacted = _DOUBLE_QUOTED_PUBLIC_CREDENTIAL_KEY_VALUE_RE.sub(
        r'\1"[redacted]"',
        redacted,
    )
    redacted = _SINGLE_QUOTED_PUBLIC_CREDENTIAL_KEY_VALUE_RE.sub(
        r"\1'[redacted]'",
        redacted,
    )
    redacted = _UNQUOTED_PUBLIC_CREDENTIAL_KEY_VALUE_RE.sub(r"\1[redacted]", redacted)
    redacted = _DOUBLE_QUOTED_KEY_VALUE_SECRET_RE.sub(r'\1"[redacted]"', redacted)
    redacted = _SINGLE_QUOTED_KEY_VALUE_SECRET_RE.sub(r"\1'[redacted]'", redacted)
    redacted = _UNQUOTED_KEY_VALUE_SECRET_RE.sub(r"\1[redacted]", redacted)
    redacted = _SESSION_ASSIGNMENT_RE.sub(r"\1[redacted]", redacted)
    if len(redacted) > MAX_TOOL_PREVIEW:
        return f"{redacted[:MAX_TOOL_PREVIEW - 3]}..."
    return redacted
