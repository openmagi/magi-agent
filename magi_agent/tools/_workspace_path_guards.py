"""Single home for the workspace path-guard primitives.

The sensitive-path-part regex, the ``**`` glob matcher, the read-offset coercer,
and the sensitive-workspace-path check were copy-pasted byte-for-byte across the
gate1a / gate5b toolhosts and ``tools/local_readonly.py`` (and, for the glob
matcher, ``tools/core_toolhost.py``). This leaf is their single home so the guard
grammar cannot drift across the read-only surfaces.

Dependency-free (stdlib only) so any tool or gate module may import it without a
cycle. Note: ``tools/local_readonly.py`` keeps its own, deliberately broader
``_SENSITIVE_PATH_PART_RE`` (different alternations and separators); that one is
NOT the same object and is intentionally left separate.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
import re

SENSITIVE_PATH_PART_RE = re.compile(
    r"(^\.|(?:^|[-_.])(?:auth|config|cookie|credential|env|key|kube|kubeconfig|password|"
    r"secret|session|token)(?:[-_.]|$))",
    re.IGNORECASE,
)


def is_sensitive_workspace_path(relative_path: Path) -> bool:
    for part in relative_path.parts:
        if not part or part in {".", ".."}:
            return True
        if part.startswith("."):
            return True
        if SENSITIVE_PATH_PART_RE.search(part):
            return True
    return False


def glob_pattern_matches(relative: str, pattern: str) -> bool:
    if pattern in {"**", "**/*"}:
        return True
    if pattern.startswith("**/"):
        suffix = pattern[3:]
        return fnmatch.fnmatchcase(relative, suffix) or fnmatch.fnmatchcase(relative, pattern)
    if "/" not in pattern and "/" in relative:
        return False
    return fnmatch.fnmatchcase(relative, pattern)


def read_offset(value: object) -> int:
    if isinstance(value, bool):
        return 1
    if isinstance(value, int) and value >= 1:
        return value
    if isinstance(value, str) and value.strip().isdecimal():
        parsed = int(value.strip())
        return parsed if parsed >= 1 else 1
    return 1


__all__ = [
    "SENSITIVE_PATH_PART_RE",
    "glob_pattern_matches",
    "is_sensitive_workspace_path",
    "read_offset",
]
