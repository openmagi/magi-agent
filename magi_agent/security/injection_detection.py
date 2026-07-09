"""Deterministic injection detector for untrusted tool-result content.

U5: Pure module, no wiring. Called by U6 (injection_guard wiring).

Scans text for 9 pattern classes of prompt-injection heuristics:
  1. Instruction override phrases (EN + KO)
  2. Role / system spoofing markers
  3. Exfiltration directives (imperative send/post/upload to URL/email)
  4. Tool / command lures (imperative fenced shell, curl|sh shaped)
  5. Credential harvesting phrases
  6. Hidden-text carriers (HTML comments with payload, zero-width chars, bidi, data-URI)
  7. Authority / urgency framing addressed to the assistant
  8. Korean-language variants of classes 1-5
  9. Advisory-marker spoofing (INJECTION_MARKER appearing inside fetched content)

Design invariants:
- Deterministic: no randomness, no I/O, no time.
- 64 KB input cap: only the first 65536 bytes (decoded as UTF-8 characters) are
  scanned.
- FPR discipline: security articles QUOTING attack strings must not produce HIGH
  findings. Severity ladder is conservative; only move up with measured data.
- No LLM judge. Pattern matching only.
- Excerpts: bounded at 160 characters, control characters stripped (Unicode Cc).
"""

from __future__ import annotations

import re
import unicodedata
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Quote-context detection
# ---------------------------------------------------------------------------
# FPR discipline: if a match falls entirely inside a quoted string literal
# (double-quotes, single-quotes, or backtick-fenced code), it is a
# DESCRIPTION or EXAMPLE of an attack, not an attack itself.  Downgrade
# severity from "high" to "medium" in that case.
#
# This handles the primary benign FP source: security articles writing
#   "ignore previous instructions"
# and code files writing
#   r"ignore previous instructions"
# as string literals.
#
# Limitation: only catches simple balanced-quote wrapping.  An attacker who
# puts their payload inside a quote still triggers a finding, just at medium
# severity.  The benign set test asserts zero HIGH on those samples.
# ---------------------------------------------------------------------------

# Pre-built pattern for quoted / code-fenced contexts
_QUOTED_CONTEXT_RE = re.compile(
    r'"[^"]*"|'          # double-quoted string
    r"'[^']*'|"          # single-quoted string
    r"`[^`]*`|"          # backtick span
    r"r\"[^\"]*\"|"      # Python raw double string
    r"r'[^']*'",         # Python raw single string
    re.DOTALL,
)


def _span_inside_quote(text: str, start: int, end: int) -> bool:
    """Return True if the span [start, end) falls entirely inside a quoted context."""
    for m in _QUOTED_CONTEXT_RE.finditer(text):
        if m.start() <= start and end <= m.end():
            return True
    return False

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

INJECTION_MARKER: str = "[magi injection_guard]"
INJECTION_MARKER_NEUTRALIZED: str = "[magi injection_guard - quoted]"

# 64 KB cap (characters, not bytes, to avoid splitting multibyte sequences)
_SCAN_CAP: int = 64 * 1024

_EXCERPT_MAX: int = 160
_EXCERPT_CONTEXT: int = 60  # chars to include on each side of the match


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class InjectionFinding(NamedTuple):
    """A single pattern match within scanned content.

    Attributes:
        pattern_id: Identifies the class and sub-pattern, e.g. ``"class1_override"``.
        severity: One of ``"high"``, ``"medium"``, ``"low"``.
        excerpt: Bounded, control-char-stripped context around the match (<= 160 chars).
        span: (start, end) offsets in the ORIGINAL text (before the 64 KB cap slice;
              relative to the capped slice if text was truncated).
    """

    pattern_id: str
    severity: str
    excerpt: str
    span: tuple[int, int]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _strip_control_chars(text: str) -> str:
    """Remove Unicode Cc (control) characters from *text*."""
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cc")


def _make_excerpt(text: str, start: int, end: int) -> str:
    """Build a bounded, control-char-stripped excerpt around the match span."""
    excerpt_start = max(0, start - _EXCERPT_CONTEXT)
    excerpt_end = min(len(text), end + _EXCERPT_CONTEXT)
    raw = text[excerpt_start:excerpt_end]
    cleaned = _strip_control_chars(raw)
    if len(cleaned) > _EXCERPT_MAX:
        cleaned = cleaned[:_EXCERPT_MAX]
    return cleaned


# ---------------------------------------------------------------------------
# Pattern registry
# ---------------------------------------------------------------------------
# Each entry: (pattern_id_suffix, compiled_re, severity, require_imperative)
# require_imperative is used by class 4 to distinguish HIGH vs MEDIUM.

# FPR discipline note: patterns use word-boundary anchors and require the
# COMBINATION of words to be present, not individual words like "instructions"
# or "system" in isolation. This prevents security articles discussing these
# topics from triggering HIGH findings.

_re_flags = re.IGNORECASE | re.UNICODE


# ---------------------------------------------------------------------------
# Class 1: Instruction override (HIGH)
# These require the instruction-override COMBINATION, not single words.
# ---------------------------------------------------------------------------

_CLASS1_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # "ignore/disregard ... instructions" with "previous/above/prior" in between
    (
        "class1_ignore_previous",
        re.compile(
            r"\b(ignore|disregard|forget|override)\b"
            r"[^.!?\n]{0,40}"
            r"\b(previous|above|prior|all\s+prior|all\s+previous)\b"
            r"[^.!?\n]{0,30}"
            r"\b(instruction|directive|prompt|rule|constraint)s?\b",
            _re_flags,
        ),
    ),
    # "new instructions:" or "updated instructions:" as a directive header
    (
        "class1_new_instructions",
        re.compile(
            r"\b(new|updated|revised|replacement)\s+(instruction|directive|prompt|rule)s?\s*:",
            _re_flags,
        ),
    ),
    # "you must now" as a takeover opener
    (
        "class1_you_must_now",
        re.compile(
            r"\byou\s+must\s+now\b",
            _re_flags,
        ),
    ),
    # Korean: "이전 지시 무시" (ignore previous instructions)
    (
        "class1_ko_ignore_previous",
        re.compile(
            r"(이전|위의|앞의|기존)\s*(지시|명령|지침|규칙|프롬프트|지시사항|명령어)\s*"
            r"(를|을|은|이)?\s*(무시|취소|삭제|잊|잊어|잊으세요)",
            _re_flags,
        ),
    ),
    # Korean: "새 지시사항:" (new instructions:)
    (
        "class1_ko_new_instructions",
        re.compile(
            r"새\s*(지시|명령|지침|규칙|지시사항)\s*[:：]",
            _re_flags,
        ),
    ),
]


# ---------------------------------------------------------------------------
# Class 2: Role / system spoofing (HIGH)
# Requires markers that are specific to chat templates or role-override syntax.
# Normal text does not start with "system:" as a standalone directive.
# ---------------------------------------------------------------------------

_CLASS2_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Leading "system:" or "assistant:" as a turn marker (line-start or start of text)
    (
        "class2_leading_system",
        re.compile(
            r"(?:^|\n)\s*(system|assistant)\s*:\s+",
            _re_flags,
        ),
    ),
    # Chat template tokens: [INST], [/INST], <|im_start|>, <|im_end|>
    (
        "class2_chat_template_token",
        re.compile(
            r"(\[/?INST\]|<\|im_start\|>|<\|im_end\|>|<\|eot_id\|>|<\|start_header_id\|>)",
            _re_flags,
        ),
    ),
    # Korean: "시스템 프롬프트:" as a role-spoof header
    (
        "class2_ko_system_prompt",
        re.compile(
            r"시스템\s*프롬프트\s*[:：]\s*",
            _re_flags,
        ),
    ),
]


# ---------------------------------------------------------------------------
# Class 3: Exfiltration directives (HIGH)
# Must combine an imperative verb (send/post/upload/email) with a destination
# (URL or email address) AND sensitive content word.
# Pure descriptions ("researchers observed that data was sent to...") do NOT
# match because they lack the imperative form.
# ---------------------------------------------------------------------------

_EXFIL_VERB = r"\b(send|post|upload|email|transmit|submit|forward|exfiltrate)\b"
_EXFIL_TARGET = (
    r"(?:https?://[^\s\"'<>]{3,}|ftp://[^\s\"'<>]{3,}"
    r"|[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
    r"|\bto\s+(?:an?\s+)?(?:external|remote|attacker)[^\n]{0,40})"
)
_SENSITIVE = r"\b(key|token|secret|password|credential|env|environment\s+variable|file|data|content|ssh|api)\b"

_CLASS3_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "class3_exfil_directive",
        re.compile(
            _EXFIL_VERB
            + r"[^.!?\n]{0,60}"
            + r"(?:" + _SENSITIVE + r"[^.!?\n]{0,60}" + _EXFIL_TARGET
            + r"|" + _EXFIL_TARGET + r"[^.!?\n]{0,60}" + _SENSITIVE + r")",
            _re_flags,
        ),
    ),
    # Korean: sensitive content + URL/email send directive
    (
        "class3_ko_exfil_directive",
        re.compile(
            r"(API\s*키|비밀\s*키|토큰|패스워드|비밀번호|자격증명|환경\s*변수|파일|데이터)"
            r"[^.!?\n]{0,60}"
            r"(https?://[^\s\"'<>]{3,}|[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})"
            r"[^.!?\n]{0,30}"
            r"(전송|보내|업로드|제출|전달)",
            _re_flags,
        ),
    ),
    # "upload/send X to URL" (any URL scheme incl. ftp/sftp)
    # Use [^\n]{0,80} to allow dots and other chars in paths like ~/.ssh
    (
        "class3_exfil_upload_url",
        re.compile(
            _EXFIL_VERB + r"[^\n]{0,80}"
            + r"(?:https?|ftp|sftp)://[^\s\"'<>]{3,}",
            _re_flags,
        ),
    ),
    # "email your credentials/secrets to <email address>"
    (
        "class3_email_credentials",
        re.compile(
            r"\bemail\b[^.!?\n]{0,40}"
            r"\b(credential|secret|key|password|data|file)s?\b"
            r"[^.!?\n]{0,40}"
            r"\bto\b[^.!?\n]{0,30}"
            r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
            _re_flags,
        ),
    ),
    # Korean: verb + to external server
    (
        "class3_ko_upload",
        re.compile(
            r"(외부|원격)\s*(서버|서버로|사이트|사이트에)"
            r"[^.!?\n]{0,30}"
            r"(업로드|전송|보내|제출)",
            _re_flags,
        ),
    ),
]


# ---------------------------------------------------------------------------
# Class 4: Tool / command lures (HIGH if imperative, else MEDIUM)
# The key signal is an imperative directed at the reader ("run/execute this")
# combined with dangerous shell content (curl|sh, wget|bash, etc.).
# ---------------------------------------------------------------------------

_IMPERATIVE_RUN = r"\b(run|execute|perform|carry\s+out)\b[^.!?\n]{0,20}\b(this|the\s+following|these)\b"
# Dangerous download-and-execute patterns:
# - curl/wget piped to shell via |
# - wget download followed by bash execution via && or ;
_SHELL_DANGEROUS = (
    r"(?:"
    r"(curl|wget)\s+[^\s]+\s*[|]\s*(sh|bash|zsh|dash|python|python3|perl|ruby|node)"
    r"|wget\s+[^\s]+(?:\s*&&\s*|\s*;\s*)(bash|sh|zsh|python|python3|perl|ruby|node)\s+[^\s]+"
    r")"
)

_CLASS4_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Imperative + dangerous shell: HIGH
    (
        "class4_imperative_shell",
        re.compile(
            r"(?:" + _IMPERATIVE_RUN + r"[^.!?\n]{0,100}" + _SHELL_DANGEROUS
            + r"|" + _SHELL_DANGEROUS + r"[^.!?\n]{0,60}" + _IMPERATIVE_RUN + r")",
            _re_flags,
        ),
    ),
    # Fenced code block containing curl|sh or wget|bash: HIGH (reader-addressed)
    (
        "class4_fenced_shell_dangerous",
        re.compile(
            r"```[^\n]*\n[^`]*" + _SHELL_DANGEROUS + r"[^`]*```",
            _re_flags | re.DOTALL,
        ),
    ),
    # "execute: curl ... | sh" or "run: wget ... && bash ..." without the
    # "this/the following" qualifier: HIGH
    (
        "class4_exec_curl_pipe",
        re.compile(
            r"\b(execute|run)\s*:\s*" + _SHELL_DANGEROUS,
            _re_flags,
        ),
    ),
    # "Please execute: wget ... && bash ..." (imperative prefix + dangerous cmd)
    (
        "class4_please_execute_download",
        re.compile(
            r"\b(please\s+)?(execute|run)\s*:\s*" + _SHELL_DANGEROUS,
            _re_flags,
        ),
    ),
    # Korean: "이 명령을 실행하세요: curl | sh"
    (
        "class4_ko_run_command",
        re.compile(
            r"(이|다음|아래)\s*(명령|커맨드|스크립트)\s*(을|를)?\s*(실행|수행)"
            r"[^.!?\n]{0,40}"
            + _SHELL_DANGEROUS,
            _re_flags,
        ),
    ),
    # Korean: wget/curl pipe variant with Korean imperative
    (
        "class4_ko_wget_pipe",
        re.compile(
            _SHELL_DANGEROUS + r"[^.!?\n]{0,40}"
            r"(실행|수행|해주세요|하세요)",
            _re_flags,
        ),
    ),
]


# ---------------------------------------------------------------------------
# Class 5: Credential harvesting (MEDIUM)
# Requires an imperative ("enter", "provide", "reveal", "give", "share", "tell")
# combined with a credential word. Plain "enter your name" is not a match.
# ---------------------------------------------------------------------------

_CRED_VERB = r"\b(enter|provide|reveal|give|share|tell|submit|type|supply|show)\b"
_CRED_WORD = r"\b(api\s*key|access\s*token|password|passwd|secret|credential|private\s*key|auth\s*token|bearer\s*token)\b"

_CLASS5_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "class5_credential_harvest",
        re.compile(
            _CRED_VERB + r"[^.!?\n]{0,40}" + _CRED_WORD
            + r"|" + _CRED_WORD + r"[^.!?\n]{0,20}" + _CRED_VERB,
            _re_flags,
        ),
    ),
    # Korean: "API 키를 알려주세요" / "비밀번호를 입력하세요"
    (
        "class5_ko_credential_harvest",
        re.compile(
            r"(API\s*키|비밀\s*키|액세스\s*토큰|비밀번호|패스워드|자격\s*증명)"
            r"[^.!?\n]{0,20}"
            r"(알려|입력|제공|제출|보내|공유)",
            _re_flags,
        ),
    ),
    # Korean: "비밀번호를 입력하세요" (password input request)
    (
        "class5_ko_enter_password",
        re.compile(
            r"비밀번호\s*(를|을)?\s*(입력|알려|제공)",
            _re_flags,
        ),
    ),
    # Korean: "비밀 키를 알려주세요" (secret key reveal)
    (
        "class5_ko_reveal_secret_key",
        re.compile(
            r"비밀\s*키\s*(를|을)?\s*(알려|입력|제공|보내)",
            _re_flags,
        ),
    ),
]


# ---------------------------------------------------------------------------
# Class 6: Hidden-text carriers
# - HTML comment containing a class 1-5 payload -> HIGH
# - HTML comment with no payload -> LOW
# - High density of zero-width / bidi control characters -> HIGH (density threat)
# - data-URI with script/javascript content -> HIGH
# ---------------------------------------------------------------------------

_HTML_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)
_ZERO_WIDTH_CHARS = frozenset(
    "​‌‍⁠﻿"  # zero-width space, ZWNJ, ZWJ, word-joiner, BOM
    "­"  # soft hyphen
)
_BIDI_CONTROL_CHARS = frozenset(
    "‎‏"  # LRM, RLM
    "‪‫‬‭‮"  # LRE, RLE, PDF, LRO, RLO
    "⁦⁧⁨⁩"  # LRI, RLI, FSI, PDI
)
_DATA_URI_SCRIPT_RE = re.compile(
    r"""data:\s*(?:text/(?:javascript|html)|application/(?:javascript|x-javascript))\s*[;,]""",
    _re_flags,
)
# Minimum density ratio (bidi/zero-width chars per total chars) to flag
_HIDDEN_TEXT_DENSITY_THRESHOLD = 0.05
_HIDDEN_TEXT_MIN_CHARS = 10  # Don't flag short texts for density


def _class6_scan(text: str) -> list[InjectionFinding]:
    findings: list[InjectionFinding] = []

    # HTML comments
    for m in _HTML_COMMENT_RE.finditer(text):
        comment_body = m.group(1)
        # Check if the comment contains a class 1-5 payload
        inner_findings = _scan_class1_to_5(comment_body)
        if inner_findings:
            severity = "high"
            for inner in inner_findings:
                findings.append(
                    InjectionFinding(
                        pattern_id="class6_html_comment_with_payload",
                        severity=severity,
                        excerpt=_make_excerpt(text, m.start(), m.end()),
                        span=(m.start(), m.end()),
                    )
                )
                break  # One finding per comment block
        else:
            # Carrier alone: low
            findings.append(
                InjectionFinding(
                    pattern_id="class6_html_comment_carrier",
                    severity="low",
                    excerpt=_make_excerpt(text, m.start(), m.end()),
                    span=(m.start(), m.end()),
                )
            )

    # Zero-width / bidi character density
    if len(text) >= _HIDDEN_TEXT_MIN_CHARS:
        hidden_count = sum(
            1 for ch in text if ch in _ZERO_WIDTH_CHARS or ch in _BIDI_CONTROL_CHARS
        )
        if hidden_count > 0:
            density = hidden_count / len(text)
            if density >= _HIDDEN_TEXT_DENSITY_THRESHOLD:
                # Find first hidden char position
                first_pos = next(
                    (i for i, ch in enumerate(text) if ch in _ZERO_WIDTH_CHARS or ch in _BIDI_CONTROL_CHARS),
                    0,
                )
                findings.append(
                    InjectionFinding(
                        pattern_id="class6_hidden_text_density",
                        severity="high",
                        excerpt=_make_excerpt(text, first_pos, first_pos + 1),
                        span=(first_pos, min(first_pos + 20, len(text))),
                    )
                )
            else:
                # Low density: low severity (bidi chars are sometimes legitimate)
                first_pos = next(
                    (i for i, ch in enumerate(text) if ch in _BIDI_CONTROL_CHARS),
                    None,
                )
                if first_pos is not None:
                    findings.append(
                        InjectionFinding(
                            pattern_id="class6_bidi_control_chars",
                            severity="low",
                            excerpt=_make_excerpt(text, first_pos, first_pos + 1),
                            span=(first_pos, min(first_pos + 5, len(text))),
                        )
                    )

    # data-URI with script content: HIGH
    for m in _DATA_URI_SCRIPT_RE.finditer(text):
        findings.append(
            InjectionFinding(
                pattern_id="class6_data_uri_script",
                severity="high",
                excerpt=_make_excerpt(text, m.start(), m.end()),
                span=(m.start(), m.end()),
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Class 7: Authority / urgency framing (MEDIUM)
# Must specifically address the assistant ("you are required to", "as your
# developer/admin") to avoid flagging normal authority claims in text.
# ---------------------------------------------------------------------------

_CLASS7_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # "as your developer/admin/operator, you are required/ordered to"
    (
        "class7_authority_framing",
        re.compile(
            r"\bas\s+your\s+(developer|admin|administrator|operator|owner|creator)\b"
            r"[^.!?\n]{0,60}"
            r"\b(you\s+are\s+required|you\s+must|you\s+are\s+ordered|I\s+(am\s+)?(ordering|commanding|requiring)\s+you)\b",
            _re_flags,
        ),
    ),
    # "authorized override from your operator" addressed to the assistant
    (
        "class7_authorized_override",
        re.compile(
            r"\b(authorized|official)\s+(override|command|directive|instruction)\b"
            r"[^.!?\n]{0,40}"
            r"\b(from\s+your|by\s+your)\s+(operator|developer|admin|owner)\b",
            _re_flags,
        ),
    ),
    # Korean: "당신의 관리자로서 당신은 즉시..." (as your admin, you must immediately...)
    (
        "class7_ko_authority",
        re.compile(
            r"당신의\s*(관리자|개발자|운영자|소유자|주인)\s*(로서|으로서|로|으로)"
            r"[^.!?\n]{0,40}"
            r"(즉시|당장|반드시|꼭|지금|공개|알려|실행|수행)",
            _re_flags,
        ),
    ),
]


# ---------------------------------------------------------------------------
# Class 8: Additional Korean variants not already caught by classes 1-7
# Patterns that are distinctly Korean phrasing without an EN equivalent match.
# ---------------------------------------------------------------------------

_CLASS8_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Korean: "시스템 프롬프트를 무시하고" (ignore system prompt and)
    (
        "class8_ko_ignore_system",
        re.compile(
            r"시스템\s*프롬프트\s*(를|을)?\s*(무시|취소|삭제|바꿔|변경)",
            _re_flags,
        ),
    ),
    # Korean: "이 셸 명령을 실행" (execute this shell command)
    (
        "class8_ko_execute_shell",
        re.compile(
            r"(이|다음|아래)\s*(셸|쉘|shell)\s*(명령|커맨드)\s*(을|를)?\s*(실행|수행)",
            _re_flags,
        ),
    ),
    # Korean: "제한을 해제" (unlock / remove restrictions)
    (
        "class8_ko_remove_restrictions",
        re.compile(
            r"(제한|규칙|규제|안전\s*장치)\s*(을|를)?\s*(해제|제거|무시|없애|풀어)",
            _re_flags,
        ),
    ),
]


# Severity mapping for class 8 (mirrors severity of the underlying class)
_CLASS8_SEVERITY: dict[str, str] = {
    "class8_ko_ignore_system": "high",
    "class8_ko_execute_shell": "high",
    "class8_ko_remove_restrictions": "medium",
}


# ---------------------------------------------------------------------------
# Class 9: Advisory-marker spoofing (MEDIUM)
# The runtime's own [magi injection_guard] marker appearing inside fetched
# content is itself an injection technique (trust-banner spoofing).
# Also catches close homoglyph/spacing variants.
# ---------------------------------------------------------------------------

_CLASS9_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Exact marker
    (
        "class9_marker_spoof_exact",
        re.compile(
            re.escape(INJECTION_MARKER),
            _re_flags,
        ),
    ),
    # Spacing/homoglyph variants: extra spaces between words
    (
        "class9_marker_spoof_spacing",
        re.compile(
            r"\[\s*magi\s+injection[_\-\s]guard\s*\]",
            _re_flags,
        ),
    ),
]


# ---------------------------------------------------------------------------
# Internal scan helpers
# ---------------------------------------------------------------------------


def _scan_pattern_list(
    text: str,
    patterns: list[tuple[str, re.Pattern[str]]],
    severity: str,
    severity_map: dict[str, str] | None = None,
) -> list[InjectionFinding]:
    """Match each pattern in *patterns* against *text*; deduplicate by (id, span).

    FPR discipline: if the match falls entirely inside a quoted string or code
    literal, severity is capped at "medium" regardless of the nominal severity.
    This prevents security articles quoting attack strings from producing HIGH
    findings.
    """
    findings: list[InjectionFinding] = []
    seen: set[tuple[str, int, int]] = set()
    for pattern_id, compiled in patterns:
        for m in compiled.finditer(text):
            key = (pattern_id, m.start(), m.end())
            if key in seen:
                continue
            seen.add(key)
            sev = severity_map.get(pattern_id, severity) if severity_map else severity
            # Downgrade HIGH to "medium" when the match is inside a quoted context
            # (description/example, not a live attack directive).
            if sev == "high" and _span_inside_quote(text, m.start(), m.end()):
                sev = "medium"
            findings.append(
                InjectionFinding(
                    pattern_id=pattern_id,
                    severity=sev,
                    excerpt=_make_excerpt(text, m.start(), m.end()),
                    span=(m.start(), m.end()),
                )
            )
    return findings


def _scan_class1_to_5(text: str) -> list[InjectionFinding]:
    """Scan only classes 1-5 (used internally by class 6 to check comment payloads)."""
    findings: list[InjectionFinding] = []
    findings.extend(_scan_pattern_list(text, _CLASS1_PATTERNS, "high"))
    findings.extend(_scan_pattern_list(text, _CLASS2_PATTERNS, "high"))
    findings.extend(_scan_pattern_list(text, _CLASS3_PATTERNS, "high"))
    findings.extend(_scan_pattern_list(text, _CLASS4_PATTERNS, "high"))
    findings.extend(_scan_pattern_list(text, _CLASS5_PATTERNS, "medium"))
    return findings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan_untrusted_content(text: str) -> tuple[InjectionFinding, ...]:
    """Scan *text* for prompt-injection heuristics.

    Returns a tuple of :class:`InjectionFinding` instances, one per pattern
    match. An empty tuple means no patterns matched. The result is deterministic:
    the same input always produces the same output.

    Only the first 64 KB of *text* is scanned. Excerpts are bounded to 160
    characters and stripped of Unicode Cc control characters.

    No I/O, no network, no randomness. Safe to call from any context.
    """
    if not text:
        return ()

    # Apply 64 KB cap
    capped = text[:_SCAN_CAP]

    all_findings: list[InjectionFinding] = []

    # Classes 1-5
    all_findings.extend(_scan_pattern_list(capped, _CLASS1_PATTERNS, "high"))
    all_findings.extend(_scan_pattern_list(capped, _CLASS2_PATTERNS, "high"))
    all_findings.extend(_scan_pattern_list(capped, _CLASS3_PATTERNS, "high"))
    all_findings.extend(_scan_pattern_list(capped, _CLASS4_PATTERNS, "high"))
    all_findings.extend(_scan_pattern_list(capped, _CLASS5_PATTERNS, "medium"))

    # Class 6: hidden-text carriers (custom logic)
    all_findings.extend(_class6_scan(capped))

    # Class 7: authority framing
    all_findings.extend(_scan_pattern_list(capped, _CLASS7_PATTERNS, "medium"))

    # Class 8: Korean variants
    all_findings.extend(
        _scan_pattern_list(capped, _CLASS8_PATTERNS, "high", _CLASS8_SEVERITY)
    )

    # Class 9: marker spoofing
    all_findings.extend(_scan_pattern_list(capped, _CLASS9_PATTERNS, "medium"))

    # Sort by span start for deterministic ordering
    all_findings.sort(key=lambda f: (f.span[0], f.pattern_id))

    return tuple(all_findings)


def neutralize_marker_spoofs(text: str) -> str:
    """Rewrite any occurrences of the runtime marker inside *text*.

    Any occurrence of ``INJECTION_MARKER`` (or close spacing variants)
    within *text* is replaced with ``INJECTION_MARKER_NEUTRALIZED``, so
    fetched content cannot wear the runtime's own trusted banner.

    This function operates on the RAW body text before the genuine header
    is prepended by U6. The genuine prepended header is added AFTER this
    call, so the neutralizer can safely replace ALL occurrences in the body.

    Idempotent: calling twice on already-neutralized text is a no-op.
    Pure (no I/O).
    """
    # Replace all class-9 variants (spacing variants first, then exact)
    # Use the same patterns as class-9 detection
    result = text
    for _pattern_id, compiled in _CLASS9_PATTERNS:
        result = compiled.sub(INJECTION_MARKER_NEUTRALIZED, result)
    return result


__all__ = [
    "INJECTION_MARKER",
    "INJECTION_MARKER_NEUTRALIZED",
    "InjectionFinding",
    "neutralize_marker_spoofs",
    "scan_untrusted_content",
]
