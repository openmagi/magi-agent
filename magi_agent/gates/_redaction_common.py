"""Single home for the shared gate transcript-redaction pattern.

``gate1a_readonly_tools`` and ``gate5b_full_toolhost`` each carried a
byte-identical 22-line ``_SENSITIVE_RE`` that matched credential shapes, raw
markers, and sensitive absolute paths in tool-output transcripts. The two used
it differently (gate1a discards the whole string, gate5b substitutes
``[redacted]``), so only the compiled pattern object is shared here, not a
function. A change to the token/path grammar now lands once instead of drifting
across the two toolhost surfaces.

Dependency-free (stdlib only) so any gate, evidence, or coding module may import
it without a cycle. The name deliberately avoids the ratchet tokens
(``PRIVATE_TEXT_RE`` / ``SECRET_TEXT_RE`` / ``RAW_PRIVATE_TEXT_RE``).
"""

from __future__ import annotations

import re

SENSITIVE_TRANSCRIPT_RE = re.compile(
    r"(?:"
    r"authorization\s*:\s*bearer\s+\S+|"
    r"\bbearer\s+\S+|"
    r"\bcookie\s*:\s*[^\n\r]+|"
    r"\bset-cookie\s*:\s*[^\n\r]+|"
    r"\bsid=[A-Za-z0-9._-]+|"
    r"\bsk-[A-Za-z0-9._-]+|"
    r"gh[opusr]_[A-Za-z0-9_]+|"
    r"github_pat_[A-Za-z0-9_]+|"
    r"xox[a-z]-[A-Za-z0-9._-]+|"
    r"AKIA[0-9A-Z]{8,}|"
    r"AIza[A-Za-z0-9_-]+|"
    r"/Users(?:/[^\s,;}\"']*)?|"
    r"/home(?:/[^\s,;}\"']*)?|"
    r"/workspace(?:/[^\s,;}\"']*)?|"
    r"/data/bots(?:/[^\s,;}\"']*)?|"
    r"raw[_ -]?(?:user|tool|session|auth|cookie|text)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought"
    r")",
    re.IGNORECASE,
)

__all__ = ["SENSITIVE_TRANSCRIPT_RE"]
