"""Tests for scripts/generate_module_map.py.

Runs the generator on the actual magi_agent codebase and validates
the output structure, completeness, and known module relationships.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent.parent
PACKAGE_DIR = ROOT_DIR / "magi_agent"
SCRIPT = ROOT_DIR / "scripts" / "generate_module_map.py"


@pytest.fixture(scope="module")
def generated_output() -> str:
    """Run the generator and return stdout."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(ROOT_DIR),
    )
    assert result.returncode == 0, f"Generator failed: {result.stderr}"
    assert result.stdout.strip(), "Generator produced empty output"
    return result.stdout


# ---------------------------------------------------------------------------
# Output is valid Markdown
# ---------------------------------------------------------------------------


class TestMarkdownStructure:
    def test_starts_with_heading(self, generated_output: str) -> None:
        assert generated_output.startswith("# Module Purpose Map (auto-generated)")

    def test_has_dependency_graph_section(self, generated_output: str) -> None:
        assert "## Dependency Graph" in generated_output

    def test_has_packages_section(self, generated_output: str) -> None:
        assert "## Packages" in generated_output


# ---------------------------------------------------------------------------
# Mermaid diagram validation
# ---------------------------------------------------------------------------


class TestMermaidDiagram:
    def test_mermaid_block_present(self, generated_output: str) -> None:
        assert "```mermaid" in generated_output
        # Count opening and closing fences
        mermaid_start = generated_output.index("```mermaid")
        rest = generated_output[mermaid_start + len("```mermaid"):]
        assert "```" in rest, "Mermaid block not closed"

    def test_mermaid_graph_type(self, generated_output: str) -> None:
        mermaid_start = generated_output.index("```mermaid")
        mermaid_end = generated_output.index("```", mermaid_start + 10)
        block = generated_output[mermaid_start:mermaid_end]
        assert "graph LR" in block

    def test_mermaid_has_edges(self, generated_output: str) -> None:
        mermaid_start = generated_output.index("```mermaid")
        mermaid_end = generated_output.index("```", mermaid_start + 10)
        block = generated_output[mermaid_start:mermaid_end]
        assert "-->" in block, "Mermaid diagram has no edges"


# ---------------------------------------------------------------------------
# All packages are represented
# ---------------------------------------------------------------------------


class TestPackageCoverage:
    def test_all_packages_present(self, generated_output: str) -> None:
        """Every sub-directory with __init__.py should have a ### heading."""
        expected_packages: set[str] = set()
        for dirpath, _dirnames, filenames in os.walk(PACKAGE_DIR):
            if "__init__.py" in filenames:
                rel = Path(dirpath).relative_to(PACKAGE_DIR)
                if rel == Path("."):
                    expected_packages.add("(root)")
                else:
                    expected_packages.add(str(rel))

        # Extract ### headings from output
        heading_pattern = re.compile(r"^### (.+?)/?$", re.MULTILINE)
        found_headings = {m.group(1) for m in heading_pattern.finditer(generated_output)}

        for pkg in expected_packages:
            assert pkg in found_headings, f"Package '{pkg}' not found in output headings"


# ---------------------------------------------------------------------------
# Table row validation
# ---------------------------------------------------------------------------


class TestTableRows:
    def test_table_header_format(self, generated_output: str) -> None:
        """Each package section should have a proper table header."""
        headers = re.findall(
            r"\| Module \| Purpose \| Depends On \| Depended By \|",
            generated_output,
        )
        assert len(headers) > 0, "No table headers found"

    def test_table_separator_format(self, generated_output: str) -> None:
        separators = re.findall(r"\|---\|---\|---\|---\|", generated_output)
        assert len(separators) > 0, "No table separators found"

    def test_all_data_rows_have_four_columns(self, generated_output: str) -> None:
        """Every data row (not header/separator) should have exactly 4 pipe-separated columns."""
        # Match rows that start with | and contain a .py filename
        row_pattern = re.compile(r"^\| (\S+\.py) \|", re.MULTILINE)
        rows = row_pattern.findall(generated_output)
        assert len(rows) > 10, f"Too few data rows found: {len(rows)}"

        # Check each row has exactly 5 pipe characters (4 columns: | c1 | c2 | c3 | c4 |)
        data_row_pattern = re.compile(r"^\| \S+\.py \|.*\|.*\|.*\|$", re.MULTILINE)
        data_rows = data_row_pattern.findall(generated_output)
        assert len(data_rows) == len(rows), (
            f"Some rows have wrong column count: {len(data_rows)} valid vs {len(rows)} total"
        )


# ---------------------------------------------------------------------------
# Spot-check known modules
# ---------------------------------------------------------------------------


class TestKnownModules:
    def _find_row(self, output: str, package: str, filename: str) -> str | None:
        """Find a table row for a specific module in a specific package section."""
        # Find the package section
        pkg_heading = f"### {package}/"
        idx = output.find(pkg_heading)
        if idx == -1:
            return None
        # Find the next ### or end
        next_heading = output.find("\n### ", idx + len(pkg_heading))
        section = output[idx:next_heading] if next_heading != -1 else output[idx:]
        # Find the row
        for line in section.split("\n"):
            if line.startswith(f"| {filename} |"):
                return line
        return None

    def test_dispatcher_depends_on_registry(self, generated_output: str) -> None:
        row = self._find_row(generated_output, "tools", "dispatcher.py")
        assert row is not None, "dispatcher.py not found in tools/ section"
        # Split into columns
        cols = [c.strip() for c in row.split("|") if c.strip()]
        assert len(cols) == 4
        depends_on = cols[2]  # "Depends On" column
        assert "registry" in depends_on, f"dispatcher.py Depends On missing 'registry': {depends_on}"

    def test_dispatcher_depends_on_permission(self, generated_output: str) -> None:
        row = self._find_row(generated_output, "tools", "dispatcher.py")
        assert row is not None
        cols = [c.strip() for c in row.split("|") if c.strip()]
        assert "permission" in cols[2]

    def test_concurrent_dispatcher_has_docstring(self, generated_output: str) -> None:
        row = self._find_row(generated_output, "tools", "concurrent_dispatcher.py")
        assert row is not None
        cols = [c.strip() for c in row.split("|") if c.strip()]
        purpose = cols[1]
        assert purpose != "\u2014", "concurrent_dispatcher.py should have a docstring"
        assert "concurrent" in purpose.lower() or "dispatcher" in purpose.lower()

    def test_concurrent_dispatcher_depended_by_adk(self, generated_output: str) -> None:
        row = self._find_row(generated_output, "tools", "concurrent_dispatcher.py")
        assert row is not None
        cols = [c.strip() for c in row.split("|") if c.strip()]
        depended_by = cols[3]
        assert "adk_bridge" in depended_by, (
            f"concurrent_dispatcher.py should be depended on by adk_bridge: {depended_by}"
        )

    def test_init_files_present(self, generated_output: str) -> None:
        """__init__.py files should appear in the tables."""
        assert "| __init__.py |" in generated_output

    def test_root_modules_present(self, generated_output: str) -> None:
        """Root-level app.py and main.py should appear."""
        root_section_idx = generated_output.find("### (root)")
        assert root_section_idx != -1
        next_section = generated_output.find("\n### ", root_section_idx + 10)
        root_section = generated_output[root_section_idx:next_section]
        assert "app.py" in root_section
        assert "main.py" in root_section


# ---------------------------------------------------------------------------
# Module count sanity check
# ---------------------------------------------------------------------------


class TestCompleteness:
    def test_module_count_reasonable(self, generated_output: str) -> None:
        """The output should reference a significant number of .py files."""
        row_pattern = re.compile(r"^\| (\S+\.py) \|", re.MULTILINE)
        rows = row_pattern.findall(generated_output)
        # We know there are ~195 .py files
        assert len(rows) >= 100, f"Only {len(rows)} modules found, expected >= 100"

    def test_no_empty_purpose_for_docstringed_files(self, generated_output: str) -> None:
        """At least some modules should have non-dash purpose."""
        row_pattern = re.compile(r"^\| \S+\.py \| (.+?) \|", re.MULTILINE)
        purposes = row_pattern.findall(generated_output)
        non_dash = [p for p in purposes if p.strip() != "\u2014"]
        assert len(non_dash) > 0, "No modules have docstrings"
