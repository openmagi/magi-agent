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


def redact_secret_tokens(text: str) -> str:
    """Apply all token/secret redaction patterns to *text* WITHOUT length truncation.

    This is the single source of truth for secret-token redaction — shared by
    both ``sanitize_tool_preview`` (which adds a 400-char length cap afterwards)
    and ``_redact_snapshot_content`` in ``memory/prompt_projection.py`` (which
    uses the projection's own ``max_bytes`` budget as the only length bound).

    Patterns covered:
      - Bearer tokens (``Authorization: Bearer …``)
      - Authorization headers (``Authorization: <scheme> <token>``)
      - Cookie / Set-Cookie headers
      - GitHub tokens (``ghp_``, ``gho_``, ``ghs_``, ``ghu_``, ``ghr_``)
      - OpenAI keys (``sk-proj-…``, ``sk-…``)
      - Stripe keys (``sk_live_…``, ``rk_test_…``, etc.)
      - Quoted/unquoted public-credential key=value pairs
      - Quoted/unquoted generic secret key=value pairs
        (``api_key``, ``secret``, ``token``, ``client_secret``, ``session_key``, …)
      - Bare ``session = <value>`` assignments
    """
    redacted = _BEARER_TOKEN_RE.sub(r"\1[redacted]", text)
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
    return redacted


def sanitize_tool_preview(preview: str) -> str:
    # ReDoS guard: pre-truncate input so catastrophic-backtracking regexes
    # cannot run over unbounded strings.  The output is always ≤ MAX_TOOL_PREVIEW
    # (~400 chars); redaction substitutions ("[redacted]") are never longer than
    # matched tokens, so a ceiling of MAX_TOOL_PREVIEW + 200 characters is safe.
    _INPUT_LIMIT = MAX_TOOL_PREVIEW + 200
    if len(preview) > _INPUT_LIMIT:
        preview = preview[:_INPUT_LIMIT]
    redacted = redact_secret_tokens(preview)
    if len(redacted) > MAX_TOOL_PREVIEW:
        return f"{redacted[:MAX_TOOL_PREVIEW - 3]}..."
    return redacted
