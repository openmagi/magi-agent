from __future__ import annotations

# B2: the secret-token redaction primitives (the token/cookie patterns, the
# quoted/unquoted key=value credential + secret patterns, and the generic
# ``redact_secret_tokens`` function) now live in the single home
# ``magi_agent.ops.safety``. This module re-exports them under their historical
# private names so existing consumers keep working unchanged, and binds
# ``redact_secret_tokens`` to the SAME kernel function object (the redaction
# identity the parity tests assert with ``is``). Several modules (notably
# ``magi_agent.transport.sse``) reach these regex objects by attribute
# (``_tool_preview._DOUBLE_QUOTED_PUBLIC_CREDENTIAL_KEY_VALUE_RE`` etc.), so all
# twelve must remain exported here. ``re`` is no longer imported: the patterns are
# owned by the kernel, so there is no local copy that could drift.
from magi_agent.ops.safety import (
    AUTHORIZATION_HEADER_RE as _AUTHORIZATION_HEADER_RE,
    BEARER_TOKEN_RE as _BEARER_TOKEN_RE,
    COOKIE_HEADER_VALUE_RE as _COOKIE_HEADER_RE,
    DOUBLE_QUOTED_KEY_VALUE_SECRET_RE as _DOUBLE_QUOTED_KEY_VALUE_SECRET_RE,
    DOUBLE_QUOTED_PUBLIC_CREDENTIAL_KEY_VALUE_RE as _DOUBLE_QUOTED_PUBLIC_CREDENTIAL_KEY_VALUE_RE,
    GITHUB_TOKEN_RE as _GITHUB_TOKEN_RE,
    MAX_TOOL_PREVIEW as MAX_TOOL_PREVIEW,
    OPENAI_TOKEN_RE as _OPENAI_TOKEN_RE,
    SINGLE_QUOTED_KEY_VALUE_SECRET_RE as _SINGLE_QUOTED_KEY_VALUE_SECRET_RE,
    SINGLE_QUOTED_PUBLIC_CREDENTIAL_KEY_VALUE_RE as _SINGLE_QUOTED_PUBLIC_CREDENTIAL_KEY_VALUE_RE,
    STRIPE_TOKEN_RE as _STRIPE_TOKEN_RE,
    UNQUOTED_KEY_VALUE_SECRET_RE as _UNQUOTED_KEY_VALUE_SECRET_RE,
    UNQUOTED_PUBLIC_CREDENTIAL_KEY_VALUE_RE as _UNQUOTED_PUBLIC_CREDENTIAL_KEY_VALUE_RE,
    redact_secret_tokens as redact_secret_tokens,
)


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
