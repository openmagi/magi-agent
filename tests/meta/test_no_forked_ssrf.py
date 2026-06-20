"""C-6 / C-7 meta-test: forbid forked SSRF classifiers.

After the C-6 consolidation, the metadata-host frozenset, the legacy-IPv4
parser (the ``0x`` / octal / decimal-packed / 2-3-part-packed forms up to
``0xFFFFFFFF``), and the URL classifier all live in
:mod:`magi_agent.security.ssrf`. Any re-fork would reopen the same
drift-on-fix hazard C-6 closes (a hardening fix to one copy leaves the
others exploitable).

AST-based: we forbid:

* A top-level identifier named ``_METADATA_HOSTS`` (or ``METADATA_HOSTS``)
  assigned to a frozenset/set literal outside ``security/ssrf.py``.
* A top-level function named ``_coerce_legacy_ipv4_address`` or
  ``_parse_legacy_ipv4_part`` outside ``security/ssrf.py`` (the new home
  exposes them as ``coerce_ip`` and a private helper).
* Source containing the magic literal ``0xFFFFFFFF`` outside ``security/ssrf.py``
  in a context that screams "legacy IPv4 packed parser" (we don't ban the
  literal everywhere — it appears in unrelated bit-masks; we ban it INSIDE
  a function whose body also references ``parts[0] << 24``).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "magi_agent"

_KERNEL_FILE = "security/ssrf.py"

# These files are allowed to IMPORT from the SSRF leaf or reference its
# symbols in a re-export-shim style. They must NOT redefine the symbols.
_ALLOWED_IMPORTERS: frozenset[str] = frozenset(
    {
        "sandbox/network.py",
        "web_acquisition/policy.py",
        "channels/telegram_adapter.py",
    }
)


def _iter_module_files() -> list[Path]:
    return sorted(
        path
        for path in PACKAGE.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _forked_metadata_hosts_assignments() -> list[str]:
    """Find ``_METADATA_HOSTS = frozenset({...})`` / ``METADATA_HOSTS = ...``
    assignments outside the canonical kernel."""
    offenders: list[str] = []
    for path in _iter_module_files():
        rel = path.relative_to(PACKAGE).as_posix()
        if rel == _KERNEL_FILE:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in tree.body:
            targets: list[ast.expr]
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id in {
                    "_METADATA_HOSTS",
                    "METADATA_HOSTS",
                }:
                    offenders.append(rel)
                    break
    return offenders


def _forked_legacy_ipv4_functions() -> list[tuple[str, str]]:
    """Find ``def _coerce_legacy_ipv4_address`` / ``def _parse_legacy_ipv4_part``
    outside the canonical kernel."""
    forbidden = {"_coerce_legacy_ipv4_address", "_parse_legacy_ipv4_part"}
    offenders: list[tuple[str, str]] = []
    for path in _iter_module_files():
        rel = path.relative_to(PACKAGE).as_posix()
        if rel == _KERNEL_FILE:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if node.name in forbidden:
                    offenders.append((rel, node.name))
    return offenders


_LEGACY_IPV4_BIT_PATTERN = re.compile(r"<<\s*24\b")


def _forked_legacy_ipv4_inline_bodies() -> list[str]:
    """Find a function body that does packed-IPv4 bit-shifting outside the
    canonical kernel — even if the function name is changed."""
    offenders: list[str] = []
    for path in _iter_module_files():
        rel = path.relative_to(PACKAGE).as_posix()
        if rel == _KERNEL_FILE:
            continue
        text = path.read_text(encoding="utf-8")
        if "0xFFFFFFFF" not in text:
            continue
        if not _LEGACY_IPV4_BIT_PATTERN.search(text):
            continue
        # Both hex packed-mask AND a ``<< 24`` shift in the same file is a
        # strong "legacy IPv4 packed parser" signal.
        offenders.append(rel)
    return offenders


def test_no_forked_metadata_hosts_outside_ssrf_leaf() -> None:
    offenders = sorted(_forked_metadata_hosts_assignments())
    assert not offenders, (
        "Forked ``_METADATA_HOSTS`` / ``METADATA_HOSTS`` assignment outside "
        f"{_KERNEL_FILE}. The metadata-host set MUST live in one place "
        "(C-6) so a NAT64 / new-cloud-host hardening cannot be applied to "
        "one copy and forgotten in the others.\n"
        f"Offenders: {offenders}\n"
        "Fix: import from magi_agent.security.ssrf.METADATA_HOSTS."
    )


def test_no_forked_legacy_ipv4_parser_outside_ssrf_leaf() -> None:
    offenders = sorted(_forked_legacy_ipv4_functions())
    assert not offenders, (
        f"Forked legacy-IPv4 parser outside {_KERNEL_FILE}:\n  "
        + "\n  ".join(f"{rel}:{name}" for rel, name in offenders)
        + "\nThe ``0x`` / octal / packed-int IPv4 parser MUST live in one "
        "place (C-6). Use magi_agent.security.ssrf.coerce_ip instead."
    )


def test_no_forked_legacy_ipv4_inline_body_outside_ssrf_leaf() -> None:
    offenders = sorted(_forked_legacy_ipv4_inline_bodies())
    assert not offenders, (
        "File contains both ``0xFFFFFFFF`` AND a ``<< 24`` shift, the "
        "signature of an inline legacy-IPv4 packed parser. Refactor to use "
        "magi_agent.security.ssrf.coerce_ip.\n"
        f"Offenders: {offenders}"
    )


def test_ssrf_kernel_exports_canonical_surface() -> None:
    """Catches accidental rename/removal of the canonical SSRF symbols."""
    from magi_agent.security import ssrf

    assert hasattr(ssrf, "METADATA_HOSTS")
    assert hasattr(ssrf, "coerce_ip")
    assert hasattr(ssrf, "classify_host")
    assert hasattr(ssrf, "classify_url")
