#!/usr/bin/env python3
"""Guard HWPX output against empty/template-only documents.

The structural validator only proves that an HWPX package can be opened. This
script extracts section text directly from the package and checks that the
output contains enough markers from the requested source markdown.
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from io import BytesIO
from pathlib import Path

from lxml import etree

NS = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
}


def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[\s#*_`~>|+\-•·▪:.,;!?;()\[\]{}\"'“”‘’]+", "", text)
    return text


def strip_markdown_line(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^#{1,6}\s+", "", line)
    line = re.sub(r"^(?:[-*+•·▪]|\d+[.)])\s+", "", line)
    line = re.sub(r"`([^`]*)`", r"\1", line)
    line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
    line = re.sub(r"[*_~]+", "", line)
    line = line.replace("|", " ")
    return line.strip()


def source_markers(source: str, title: str) -> list[str]:
    candidates: list[str] = []
    if title.strip():
        candidates.append(normalize_text(title))

    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?", line):
            continue
        stripped = strip_markdown_line(line)
        normalized = normalize_text(stripped)
        if len(normalized) < 8:
            continue
        candidates.append(normalized[:120])

    deduped: list[str] = []
    seen: set[str] = set()
    for marker in candidates:
        if marker and marker not in seen:
            deduped.append(marker)
            seen.add(marker)
    return deduped


def extract_hwpx_text(hwpx_path: Path) -> str:
    with zipfile.ZipFile(hwpx_path, "r") as zf:
        section_bytes = zf.read("Contents/section0.xml")

    root = etree.parse(BytesIO(section_bytes)).getroot()
    text_nodes = root.xpath(".//hp:t", namespaces=NS)
    return "\n".join("".join(node.itertext()) for node in text_nodes)


def required_marker_count(marker_count: int, source_len: int) -> int:
    if marker_count <= 0:
        return 0
    if source_len < 200:
        return min(marker_count, 2)
    if source_len < 1000:
        return min(marker_count, 4)
    return min(marker_count, 8)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate that an HWPX output contains requested source content"
    )
    parser.add_argument("--source", required=True, help="Source markdown path")
    parser.add_argument("--output", required=True, help="Output HWPX path")
    parser.add_argument("--title", default="", help="Requested document title")
    args = parser.parse_args()

    source_path = Path(args.source)
    output_path = Path(args.output)
    if not source_path.is_file():
        print(f"FAIL: source file not found: {source_path}", file=sys.stderr)
        return 2
    if not output_path.is_file():
        print(f"FAIL: output file not found: {output_path}", file=sys.stderr)
        return 2

    source = source_path.read_text(encoding="utf-8")
    output_text = extract_hwpx_text(output_path)
    source_norm = normalize_text(source)
    output_norm = normalize_text(output_text)

    min_chars = 8 if len(source_norm) < 80 else min(500, max(40, len(source_norm) // 20))
    if len(output_norm) < min_chars:
        print("FAIL: source content coverage too low")
        print(
            f" - extracted output text chars={len(output_norm)}, required_min={min_chars}"
        )
        return 1

    markers = source_markers(source, args.title)
    if not markers:
        print("PASS: content-guard")
        print("  No source markers required for this short source.")
        return 0

    matched = [marker for marker in markers if marker in output_norm]
    required = required_marker_count(len(markers), len(source_norm))
    if len(matched) < required:
        missing = [marker for marker in markers if marker not in output_norm][:5]
        print("FAIL: source content coverage too low")
        print(f" - matched source markers={len(matched)}/{len(markers)}, required={required}")
        if missing:
            print(" - missing examples:")
            for marker in missing:
                print(f"   - {marker[:80]}")
        return 1

    print("PASS: content-guard")
    print(
        f"  matched source markers={len(matched)}/{len(markers)}, "
        f"output_chars={len(output_norm)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
