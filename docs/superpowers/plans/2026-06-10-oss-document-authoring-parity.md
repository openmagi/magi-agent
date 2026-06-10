# OSS Document Authoring Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Use `superpowers:test-driven-development` for every behavior change and `superpowers:verification-before-completion` before claiming completion.

**Goal:** Restore hosted/legacy first-party document authoring parity in OSS Magi Agent for `DocumentWrite` and bundled `document-writer`/`hwpx` skills.

**Architecture:** Add a Python-native document write package under `magi_agent/tools/document_write/`, keep `magi_agent/tools/document_write_tools.py` as the public compatibility import, and keep the native tool entrypoint at `magi_agent.plugins.native.documents:document_write`. Implement local writers for `md`, `txt`, `html`, `docx`, `pdf`, and `hwpx`, canonical Markdown export parity for `renderer="canonical_markdown"` where local renderers are available, bundled HWPX runtime scripts/templates/guards, lazy optional dependencies, workspace-safe artifact metadata, and an OSS-native `MAGI_DOCUMENT_AGENTIC_MODEL` DOCX/HWPX authoring boundary with deterministic fallback.

**Tech Stack:** Python 3.11+, pytest, existing `python-docx` optional `files` extra, stdlib `zipfile`/`xml`/`subprocess`/`shutil`, existing Magi Agent `ToolContext`, `ToolResult`, native plugin catalog, bundled skill loader.

---

## Context

- Worktree: `/Users/kevin/Desktop/claude_code/magi-agent/.worktrees/oss-document-authoring-parity-20260610`
- Branch: `codex/oss-document-authoring-parity-20260610`
- Base: `origin/main`
- Design doc: `docs/superpowers/specs/2026-06-10-oss-document-authoring-parity-design.md`
- Hosted reference files inspected:
  - `/Users/kevin/Desktop/claude_code/clawy/src/lib/templates/skills/document-writer/SKILL.md`
  - `/Users/kevin/Desktop/claude_code/clawy/src/lib/templates/skills/hwpx/SKILL.md`
  - `/Users/kevin/Desktop/claude_code/clawy/infra/docker/clawy-core-agent/src/tools/DocumentWrite.ts`
  - `/Users/kevin/Desktop/claude_code/clawy/infra/docker/clawy-core-agent/src/tools/document/*`

## Verification Baseline

- [ ] Run the focused current baseline before implementation:

```bash
MAGI_CONFIG="$(mktemp)" uv run --extra dev --extra files pytest \
  tests/tools/test_document_write_tools.py \
  tests/test_native_plugin_catalog.py \
  tests/test_plugin_tool_projection.py \
  -q
```

- [ ] Record any pre-existing failures before editing production code.

## Task 1: Add Failing Parity Tests

- [ ] Add `tests/tools/test_document_write_parity.py`.
- [ ] Cover hosted-compatible input shapes:
  - `content`, `text`, `markdown`
  - `source` as string
  - `source` mapping with `kind`/`type`, `content`, `markdown`, `text`, and workspace-relative `path`
  - structured `source` with `blocks`
  - structured `source` with workspace-relative `blocksFile`
  - invalid source/path traversal reads
  - filename suffix inference
  - unsupported format rejection
  - `outputs` requesting multiple canonical formats
- [ ] Cover single-format writes:
  - `format="md"` writes redacted Markdown and returns `format: "md"`
  - `format="txt"` writes plain text and returns `format: "txt"`
  - `format="html"` writes escaped HTML and returns `format: "html"`
  - `format="docx"` preserves existing real DOCX behavior and metadata
  - `format="pdf"` returns `pdf_converter_unavailable` when no converter is found
  - `format="hwpx"` writes a valid HWPX ZIP with required entries
- [ ] Cover artifact metadata for successful outputs: `path`, `pathRef`, `format`, `mimeType`, `previewKind`, `contentDigest`, `byteCount`, `artifactRef`, `artifactRefs`, and `localOnly`.
- [ ] Cover canonical Markdown:
  - `renderer="canonical_markdown"` emits HTML and editable DOCX outputs
  - metadata includes `documentWriteMode`, `canonicalMarkdownQa`, and canonical output formats
  - browser-required PDF or `docxMode="fixed_layout"` returns `canonical_markdown_renderer_unavailable` when no renderer is configured
- [ ] Cover PDF conversion deterministically with monkeypatches:
  - no converter
  - converter non-zero exit
  - invalid non-PDF output
  - timeout
  - successful fake converter that writes `%PDF-` bytes
- [ ] Add HWPX validation tests for ZIP header, required package entries, content guard, validator guard, and all templates: `base`, `gonmun`, `report`, `minutes`.
- [ ] Add agentic boundary tests:
  - fake successful agentic DOCX/HWPX writer records `documentWriteMode="agentic"`
  - fake failing agentic writer falls back to deterministic writer and records `documentWriteMode="fast_fallback"`
  - `MAGI_DOCUMENT_AGENTIC_MODEL` config produces a LiteLLM-backed writer without live model calls in tests
  - reference-template HWPX blocks with `hwpx_reference_template_requires_agentic_authoring` when no agentic writer is configured
- [ ] Add bundled skill/package tests:
  - `tests/test_bundled_document_skills.py`
  - package data globs include document skills and HWPX runtime assets.
- [ ] Add projection/catalog tests:
  - `openmagi.documents` allowed formats include `md`, `txt`, `html`, `docx`, `pdf`, `hwpx`, `xlsx`, `csv`
  - `DocumentWrite` input schema exposes `format`, `renderer`, `outputs`, `docxMode`, `preset`, `page`, `locale`, `title`, `filename`, `template`, and `source`
- [ ] Run the new tests and confirm they fail for the missing implementation:

```bash
MAGI_CONFIG="$(mktemp)" uv run --extra dev --extra files pytest \
  tests/tools/test_document_write_parity.py \
  tests/test_bundled_document_skills.py \
  -q
```

## Task 2: Implement Shared DocumentWrite Model and Orchestrator

- [ ] Add `magi_agent/tools/document_write/__init__.py`.
- [ ] Add `magi_agent/tools/document_write/model.py`:
  - supported format constants
  - MIME type mapping
  - preview kind mapping
  - filename inference
  - source normalization
  - hosted structured block normalization for `blocks` and `blocksFile`
  - multi-output request normalization
  - shared artifact metadata builder
  - hosted-compatible input schema object reused by tool projection
- [ ] Add `magi_agent/tools/document_write/orchestrator.py`:
  - `document_write(arguments, context) -> ToolResult`
  - dispatch single and multi-output writes
  - preserve existing path safety through `safe_child_path`
  - preserve redaction through `redact_public_text`
  - aggregate `outputs` and `artifactRefs` for multi-format writes
- [ ] Update `magi_agent/plugins/native/documents.py` to delegate lazily to the orchestrator.
- [ ] Run the failing parity tests and confirm failures now move from dispatch/source-shape issues to missing format writers:

```bash
MAGI_CONFIG="$(mktemp)" uv run --extra dev --extra files pytest \
  tests/tools/test_document_write_parity.py \
  -q
```

## Task 3: Implement Markdown, Plain Text, and HTML Writers

- [ ] Add `magi_agent/tools/document_write/markdown.py`:
  - pragmatic block parser for headings, paragraphs, lists, code fences, and pipe tables
  - plain text extraction
  - HTML escaping
  - print-friendly HTML shell
- [ ] Add `magi_agent/tools/document_write/text.py`.
- [ ] Add `magi_agent/tools/document_write/html.py`.
- [ ] Ensure raw HTML input is escaped, not trusted.
- [ ] Run red/green tests for `md`, `txt`, and `html`:

```bash
MAGI_CONFIG="$(mktemp)" uv run --extra dev --extra files pytest \
  tests/tools/test_document_write_parity.py::TestDocumentWriteTextHtmlParity \
  -q
```

## Task 4: Preserve and Upgrade DOCX Writer Contract

- [ ] Move or wrap the existing DOCX backend without breaking imports from `magi_agent.tools.document_write_tools`.
- [ ] Keep lazy `python-docx` import behavior.
- [ ] Keep existing coverage evidence behavior.
- [ ] Expand DOCX output projection to shared parity metadata, including `mimeType` and `previewKind`.
- [ ] Add agentic writer dependency injection for DOCX, `MAGI_DOCUMENT_AGENTIC_MODEL` LiteLLM writer wiring, and deterministic fallback metadata.
- [ ] Ensure existing `tests/tools/test_document_write_tools.py` still pass.
- [ ] Run:

```bash
MAGI_CONFIG="$(mktemp)" uv run --extra dev --extra files pytest \
  tests/tools/test_document_write_tools.py \
  tests/tools/test_document_write_parity.py::TestDocumentWriteDocxParity \
  -q
```

## Task 5: Implement PDF Via DOCX Conversion

- [ ] Add `magi_agent/tools/document_write/pdf.py`.
- [ ] Find converter with `shutil.which("libreoffice")` or `shutil.which("soffice")`.
- [ ] Write a temporary DOCX in a workspace-safe temp directory.
- [ ] Run converter with `subprocess.run([...], shell=False, timeout=...)`.
- [ ] Validate output exists and begins with `%PDF-`.
- [ ] Return `pdf_converter_unavailable` when no converter is present.
- [ ] Return `document_pdf_conversion_failed`, `document_pdf_validation_failed`, or `document_pdf_conversion_timeout` for failed conversion, invalid output, or timeout.
- [ ] Run:

```bash
MAGI_CONFIG="$(mktemp)" uv run --extra dev --extra files pytest \
  tests/tools/test_document_write_parity.py::TestDocumentWritePdfParity \
  -q
```

## Task 6: Implement Deterministic HWPX Runtime

- [ ] Add `magi_agent/tools/document_write/hwpx.py`.
- [ ] Add bundled runtime assets under `magi_agent/tools/document_write/hwpx_runtime/`:
  - scripts copied/ported from hosted `runtime/hwpx/scripts/`
  - templates copied/ported from hosted `runtime/hwpx/templates/`
  - `references/hwpx-format.md`
- [ ] Build a valid HWPX ZIP with required entries:
  - `mimetype`
  - `META-INF/manifest.xml`
  - `Contents/header.xml`
  - `Contents/section0.xml`
  - `Contents/version.xml`
- [ ] Escape all XML content.
- [ ] Validate generated files before returning ok.
- [ ] Run bundled `validate.py` and `content_guard.py` for generated output.
- [ ] Run bundled `page_guard.py` for reference-template agentic output when applicable.
- [ ] Support templates `base`, `gonmun`, `report`, and `minutes`.
- [ ] Add agentic writer dependency injection for HWPX, `MAGI_DOCUMENT_AGENTIC_MODEL` LiteLLM writer wiring, and deterministic fallback metadata.
- [ ] Return clear blocked codes for unavailable runtime, invalid package, failed guards, or reference-template mutation without agentic authoring.
- [ ] Run:

```bash
MAGI_CONFIG="$(mktemp)" uv run --extra dev --extra files pytest \
  tests/tools/test_document_write_parity.py::TestDocumentWriteHwpxParity \
  -q
	```

## Task 6.5: Implement Canonical Markdown Export

- [ ] Add `magi_agent/tools/document_write/canonical.py`.
- [ ] Parse canonical Markdown into the shared block model.
- [ ] Render canonical HTML with print CSS, locale, page, and preset metadata.
- [ ] Emit QA JSON with `status`, `sourceHash`, `rendererVersion`, `warnings`, and optional `pageCount`.
- [ ] Support editable canonical DOCX through the existing optional DOCX dependency.
- [ ] Support canonical HTML output without browser infrastructure.
- [ ] Return `canonical_markdown_renderer_unavailable` for PDF or fixed-layout DOCX when no browser render provider is configured.
- [ ] Include `documentWriteMode="canonical_markdown"`, `canonicalMarkdownQa`, `canonicalMarkdownOutputs`, and artifact refs in output/metadata.
- [ ] Run:

```bash
MAGI_CONFIG="$(mktemp)" uv run --extra dev --extra files pytest \
  tests/tools/test_document_write_parity.py::TestDocumentWriteCanonicalParity \
  -q
```

## Task 7: Add First-Party Bundled Skills

- [ ] Add `magi_agent/skills/bundled/document-writer/SKILL.md`.
- [ ] Add `magi_agent/skills/bundled/hwpx/SKILL.md`.
- [ ] Update `pyproject.toml` package data globs for:
  - `skills/bundled/*/SKILL.md`
  - document writer skill assets if any
  - `tools/document_write/hwpx_runtime/**/*`
- [ ] Update `docs/skills.md` to include the two first-party document skills.
- [ ] Update generated docs only if the repo has a documented generator; otherwise do not churn `docs/llms*.txt`.
- [ ] Run:

```bash
MAGI_CONFIG="$(mktemp)" uv run --extra dev --extra files pytest \
  tests/test_bundled_document_skills.py \
  tests/test_packaging_data.py \
  -q
```

## Task 8: Catalog and Projection Parity

- [ ] Update `magi_agent/plugins/native_catalog.py` so `openmagi.documents` allowed formats include `md`, `txt`, `html`, `docx`, `pdf`, `hwpx`, `xlsx`, and `csv`.
- [ ] Update `magi_agent/plugins/tool_projection.py` so `DocumentWrite` projects the hosted-compatible input schema from `magi_agent.tools.document_write.model`.
- [ ] Add assertions to existing tests for `html`, `docx`, `hwpx`, `md`, `txt`, and `pdf`.
- [ ] Add assertions to existing tests for `format`, `renderer`, `outputs`, `docxMode`, `preset`, `page`, `locale`, `title`, `filename`, `template`, and `source`.
- [ ] Run:

```bash
MAGI_CONFIG="$(mktemp)" uv run --extra dev --extra files pytest \
  tests/test_native_plugin_catalog.py \
  tests/test_plugin_tool_projection.py \
  -q
```

## Task 9: End-to-End Verification

- [ ] Run focused full document suite:

```bash
MAGI_CONFIG="$(mktemp)" uv run --extra dev --extra files pytest \
  tests/tools/test_document_write_tools.py \
  tests/tools/test_document_write_parity.py \
  tests/test_bundled_document_skills.py \
  tests/test_packaging_data.py \
  tests/test_native_plugin_catalog.py \
  tests/test_plugin_tool_projection.py \
  -q
```

- [ ] Run lint or broader tests proportional to changed surface:

```bash
MAGI_CONFIG="$(mktemp)" uv run --extra dev ruff check \
  magi_agent/plugins/native/documents.py \
  magi_agent/tools/document_write_tools.py \
  magi_agent/tools/document_write \
  tests/tools/test_document_write_tools.py \
  tests/tools/test_document_write_parity.py \
  tests/test_bundled_document_skills.py
```

- [ ] If `ruff` is not configured through `uv`, use the repo's documented lint command instead:

```bash
npm run lint
```

## Task 10: Review, Memory, and PR

- [ ] Request subagent code review for implementation and tests.
- [ ] Fix actionable review findings with TDD.
- [ ] Update hosted project operational memory:
  - `/Users/kevin/Desktop/claude_code/clawy/SCRATCHPAD.md`
  - `/Users/kevin/Desktop/claude_code/clawy/WORKING.md`
  - `/Users/kevin/Desktop/claude_code/clawy/memory/daily/2026-06-10.md`
- [ ] Inspect final diff:

```bash
git status --short
git diff --stat
git diff --check
```

- [ ] Commit the completed change:

```bash
git add docs/superpowers/specs/2026-06-10-oss-document-authoring-parity-design.md \
  docs/superpowers/plans/2026-06-10-oss-document-authoring-parity.md \
  magi_agent plugins tests pyproject.toml docs
git commit -m "Restore document authoring parity"
```

- [ ] Push and open draft PR:

```bash
git push -u origin codex/oss-document-authoring-parity-20260610
gh pr create --draft --base main --head codex/oss-document-authoring-parity-20260610 \
  --title "Restore first-party document authoring parity" \
  --body-file /tmp/oss-document-authoring-parity-pr.md
```

- [ ] Final response must include PR URL, verification commands, and any known limitations.
