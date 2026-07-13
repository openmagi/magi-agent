"""U8 -- rename customize/prompt_injection.py to authored_prompt_append.

Tests:
1. Old import path (shim) still resolves and exposes all public symbols.
2. New import path (canonical) resolves and exposes all public symbols.
3. Grep gate: no code file (excluding the shim itself and this test) imports
   from the OLD module path; all live callers should use the new path or be
   updated in a follow-up PR (the shim absorbs them for one release).
4. UI label: reusable-conditions-tab.tsx and author-wizard.tsx no longer
   contain the old "Prompt injection" label for the prompt_injection kind.

Note: the stored custom_rule kind id ``prompt_injection`` is intentionally NOT
renamed. Data migration for a cosmetic rename carries bad risk/benefit; the
kind id is retained for backward compatibility. See authored_prompt_append.py
docstring.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. Shim: old path still resolves
# ---------------------------------------------------------------------------


def test_old_import_path_shim_resolves() -> None:
    """The deprecated shim at customize/prompt_injection.py must still export all symbols."""
    from magi_agent.customize.prompt_injection import (  # noqa: PLC0415
        VALUE_MAX,
        apply_prompt_injection_to_prompt_sections,
        apply_prompt_injection_to_tool_args,
        validate_prompt_injection_payload,
    )

    assert isinstance(VALUE_MAX, int) and VALUE_MAX == 4000
    assert callable(apply_prompt_injection_to_tool_args)
    assert callable(apply_prompt_injection_to_prompt_sections)
    assert callable(validate_prompt_injection_payload)


# ---------------------------------------------------------------------------
# 2. Canonical: new path resolves with identical symbols
# ---------------------------------------------------------------------------


def test_new_canonical_path_resolves() -> None:
    """The canonical module authored_prompt_append exports all public symbols."""
    from magi_agent.customize.authored_prompt_append import (  # noqa: PLC0415
        VALUE_MAX,
        apply_prompt_injection_to_prompt_sections,
        apply_prompt_injection_to_tool_args,
        validate_prompt_injection_payload,
    )

    assert isinstance(VALUE_MAX, int) and VALUE_MAX == 4000
    assert callable(apply_prompt_injection_to_tool_args)
    assert callable(apply_prompt_injection_to_prompt_sections)
    assert callable(validate_prompt_injection_payload)


def test_shim_and_canonical_export_same_functions() -> None:
    """Shim re-exports the exact same function objects as the canonical module."""
    import magi_agent.customize.authored_prompt_append as canonical  # noqa: PLC0415
    import magi_agent.customize.prompt_injection as shim  # noqa: PLC0415

    assert shim.apply_prompt_injection_to_tool_args is canonical.apply_prompt_injection_to_tool_args
    assert (
        shim.apply_prompt_injection_to_prompt_sections
        is canonical.apply_prompt_injection_to_prompt_sections
    )
    assert shim.validate_prompt_injection_payload is canonical.validate_prompt_injection_payload
    assert shim.VALUE_MAX is canonical.VALUE_MAX


# ---------------------------------------------------------------------------
# 3. Grep gate: no live code imports from the old module path
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """Return the repository root (parent of the magi_agent package)."""
    return Path(__file__).parent.parent


_SHIM_PATH = "magi_agent/customize/prompt_injection.py"
_OLD_IMPORT_RE = re.compile(
    r"from\s+magi_agent\.customize\.prompt_injection\s+import"
    r"|import\s+magi_agent\.customize\.prompt_injection"
)

# Files that are explicitly allowed to reference the old module path:
_ALLOWED_PATHS = {
    # The shim itself -- it is the re-export definition.
    "magi_agent/customize/prompt_injection.py",
    # This test file -- it explicitly imports the shim to verify it works.
    "tests/test_u8_authored_prompt_append_rename.py",
}


def test_grep_gate_no_new_stale_live_code_imports() -> None:
    """No new live code (non-test, non-shim) references the old module import path.

    Existing tests are grandfathered (shim keeps them green for one release).
    New production code must import from authored_prompt_append.
    """
    root = _repo_root()
    stale_production_files: list[str] = []

    for py_file in root.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        rel = py_file.relative_to(root).as_posix()
        if rel in _ALLOWED_PATHS:
            continue
        # Only flag production code (not tests) as failures.
        # Tests get a release to migrate.
        if rel.startswith("tests/"):
            continue
        try:
            text = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _OLD_IMPORT_RE.search(text):
            stale_production_files.append(rel)

    assert stale_production_files == [], (
        "Production code still imports from the deprecated "
        "magi_agent.customize.prompt_injection path. "
        "Update these imports to magi_agent.customize.authored_prompt_append:\n"
        + "\n".join(f"  {f}" for f in stale_production_files)
    )


# ---------------------------------------------------------------------------
# 4. UI label: old "Prompt injection" label gone from kind entry
# ---------------------------------------------------------------------------


def _read_ui_source(relative: str) -> str:
    root = _repo_root()
    path = root / relative
    return path.read_text(encoding="utf-8")


def test_reusable_conditions_tab_label_updated() -> None:
    """reusable-conditions-tab.tsx no longer labels prompt_injection as 'Prompt injection'."""
    src = _read_ui_source(
        "apps/web/src/components/dashboard/customize/reusable-conditions-tab.tsx"
    )
    # The old label must be gone.
    assert 'prompt_injection: "Prompt injection (mutator)"' not in src, (
        "Old label 'Prompt injection (mutator)' still present in reusable-conditions-tab.tsx"
    )
    # The new label must be present.
    assert "Prompt append (authored)" in src, (
        "New label 'Prompt append (authored)' missing from reusable-conditions-tab.tsx"
    )


def test_author_wizard_condition_meta_label_updated() -> None:
    """author-wizard.tsx CONDITION_META no longer has label 'Append context (mutator)'."""
    src = _read_ui_source(
        "apps/web/src/components/dashboard/customize/guided/author-wizard.tsx"
    )
    # Old label must be gone from the CONDITION_META block.
    # The phrase may still appear in comments, so only check the label: key.
    assert 'label: "Append context (mutator)"' not in src, (
        "Old CONDITION_META label 'Append context (mutator)' still present in author-wizard.tsx"
    )
    # New label must be present.
    assert 'label: "Prompt append (authored)"' in src, (
        "New CONDITION_META label 'Prompt append (authored)' missing from author-wizard.tsx"
    )
