"""Shared secret/path scrubber for the General Automation harness.

This module is the single source of truth for the SUPERSET of all private-text
redaction patterns previously scattered across:

  - harness/general_automation/shell_policy.py      (_PRIVATE_TEXT_RE / _safe_text)
  - harness/general_automation/output_budget_policy.py (_PRIVATE_TEXT_RE / _safe_text)

It is intentionally import-light: only ``re`` and standard-library stdlib.
No transport, socket, pydantic, or framework imports — GA policy modules that
import this must remain free of heavy transitive dependencies.

PR12 adds coverage for system-path prefixes /etc/, /proc/, /sys/, /root/ that
were missing from both prior sets (security review of PR2 path-argument handling).
"""
from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Superset of all patterns from shell_policy + output_budget_policy, plus
# the new /etc/ /proc/ /sys/ /root/ prefixes from the PR12 security review.
#
# ORDERING MATTERS: longer / more specific patterns must appear before their
# shorter prefixes where one could shadow another.  Cloud URIs (s3://, gs://,
# supabase://) precede bare path patterns; auth headers precede bare bearer.
# ---------------------------------------------------------------------------
_PRIVATE_TEXT_RE = re.compile(
    r"(?:"
    # ---- Auth headers (longest first) ----
    r"authorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=-]+|"
    r"\bbearer\s+[A-Za-z0-9._~+/=-]+|"
    r"\bcookie\s*:\s*[^\n\r]+|"
    r"\bsid=[A-Za-z0-9._-]+|"
    # ---- API key tokens ----
    r"\bsk[-_][A-Za-z0-9._-]+|"
    r"gh[opusr]_[A-Za-z0-9_]+|"
    r"github_pat_[A-Za-z0-9_]+|"
    r"xox[a-z]-[A-Za-z0-9._-]+|"
    r"AKIA[0-9A-Z]{8,}|"
    r"AIza[A-Za-z0-9_-]+|"
    # ---- Cloud storage URIs (before bare path prefixes) ----
    r"s3://[^\s,;}\"']+|"
    r"gs://[^\s,;}\"']+|"
    r"supabase://[^\s,;}\"']+|"
    # ---- Filesystem path prefixes ----
    # Original set
    r"/Users(?:/[^\s,;}\"']*)?|"
    r"/home(?:/[^\s,;}\"']*)?|"
    r"/workspace(?:/[^\s,;}\"']*)?|"
    r"/data/bots(?:/[^\s,;}\"']*)?|"
    r"/var/lib/kubelet(?:/[^\s,;}\"']*)?|"
    # PR12 additions: system-path prefixes (require trailing / to avoid over-matching)
    r"/etc/[^\s,;}\"']*|"
    r"/proc/[^\s,;}\"']*|"
    r"/sys/[^\s,;}\"']*|"
    r"/root/[^\s,;}\"']*|"
    # ---- Raw content markers ----
    # Covers all sub-types from both shell_policy and output_budget_policy.
    # Pattern: "raw" + optional separator (_, -, space) + subtype keyword.
    r"raw[_ -]?(?:tool|child|prompt|transcript|output|result|log|args|browser|dom)|"
    # ---- Internal reasoning markers ----
    r"hidden[_ -]?reasoning|"
    r"chain[_ -]?of[_ -]?thought"
    r")",
    re.IGNORECASE,
)

_REDACTED = "[redacted-private]"


def scrub_text(value: str) -> str:
    """Replace all private/secret patterns in *value* with ``[redacted-private]``.

    Implements the superset of all prior per-module ``_PRIVATE_TEXT_RE`` sets,
    extended with system-path prefixes /etc/, /proc/, /sys/, /root/.

    Pure function: import-light (regex only), no side effects.
    """
    return _PRIVATE_TEXT_RE.sub(_REDACTED, value)


__all__ = ["scrub_text"]
